#!/usr/bin/env node
"use strict";

// CI-only Chrome probe. Chrome 137+ removed --load-extension from branded
// builds. Exercise Chrome's own extensions-page directory-drop handler over a
// pipe-only DevTools connection. Unlike the session-only DevTools unpacked-load
// command, this follows the persistent user-facing extension installation path.

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn } = require("child_process");

const SESSION_COOKIE_NAME = "pilot_session";
const SESSION_COOKIE_VALUE = "offline-ci-authenticated";

function parseArguments(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key || !key.startsWith("--") || !value) {
      throw new Error(`invalid argument near ${key || "<end>"}`);
    }
    result[key.slice(2)] = value;
  }
  for (const name of [
    "chrome",
    "profile",
    "extension",
    "expected-id",
    "expected-version",
    "token-file",
    "mode",
  ]) {
    if (!result[name]) {
      throw new Error(`missing --${name}`);
    }
  }
  if (!new Set(["install-drag", "seed-existing"]).has(result.mode)) {
    throw new Error(`invalid --mode: ${result.mode}`);
  }
  return result;
}

function waitForExit(child, timeoutMilliseconds) {
  return new Promise((resolve, reject) => {
    if (child.exitCode !== null) {
      resolve(child.exitCode);
      return;
    }
    const timer = setTimeout(() => {
      reject(new Error("Chrome did not exit after Browser.close"));
    }, timeoutMilliseconds);
    child.once("exit", (code) => {
      clearTimeout(timer);
      resolve(code);
    });
  });
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  const chrome = path.resolve(args.chrome);
  const profile = path.resolve(args.profile);
  const extension = path.resolve(args.extension);
  if (!fs.statSync(chrome).isFile()) {
    throw new Error(`Chrome executable is missing: ${chrome}`);
  }
  if (!fs.statSync(path.join(extension, "manifest.json")).isFile()) {
    throw new Error(`unpacked extension is missing manifest.json: ${extension}`);
  }
  fs.mkdirSync(profile, { recursive: true });

  const child = spawn(
    chrome,
    [
      `--user-data-dir=${profile}`,
      "--remote-debugging-pipe",
      "--enable-unsafe-extension-debugging",
      "--enable-automation",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-sync",
      "about:blank",
    ],
    {
      stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"],
      // Keep the real extensions page visible for diagnostics and screenshots
      // when a hosted-runner Chrome regression rejects the directory drop.
      windowsHide: false,
    },
  );
  const protocolInput = child.stdio[3];
  const protocolOutput = child.stdio[4];
  if (!protocolInput || !protocolOutput) {
    child.kill();
    throw new Error("Node did not create Chrome remote-debugging pipes");
  }

  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr = (stderr + chunk.toString("utf8")).slice(-16000);
  });

  let nextId = 1;
  let buffer = Buffer.alloc(0);
  const pending = new Map();
  protocolOutput.on("data", (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    for (;;) {
      const separator = buffer.indexOf(0);
      if (separator < 0) {
        break;
      }
      const payload = buffer.subarray(0, separator).toString("utf8");
      buffer = buffer.subarray(separator + 1);
      if (!payload) {
        continue;
      }
      let message;
      try {
        message = JSON.parse(payload);
      } catch (error) {
        for (const request of pending.values()) {
          request.reject(new Error(`invalid Chrome protocol payload: ${error}`));
        }
        pending.clear();
        continue;
      }
      if (message.id && pending.has(message.id)) {
        const request = pending.get(message.id);
        pending.delete(message.id);
        clearTimeout(request.timer);
        if (message.error) {
          request.reject(
            new Error(
              `Chrome protocol ${request.method} failed: ${JSON.stringify(message.error)}`,
            ),
          );
        } else {
          request.resolve(message.result || {});
        }
      }
    }
  });

  child.once("exit", (code) => {
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(
        new Error(`Chrome exited before ${request.method}; code=${code}; stderr=${stderr}`),
      );
    }
    pending.clear();
  });

  function send(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Chrome protocol ${method} timed out; stderr=${stderr}`));
      }, 45000);
      pending.set(id, { method, resolve, reject, timer });
      const message = { id, method, params };
      if (sessionId) {
        message.sessionId = sessionId;
      }
      protocolInput.write(`${JSON.stringify(message)}\0`, "utf8");
    });
  }

  async function loadUnpackedThroughChromeDrop() {
    const target = await send("Target.createTarget", { url: "chrome://extensions/" });
    const attached = await send("Target.attachToTarget", {
      targetId: target.targetId,
      flatten: true,
    });
    if (!attached.sessionId) {
      throw new Error("Chrome did not return an extensions-page CDP session");
    }
    const sessionId = attached.sessionId;
    await send("Page.enable", {}, sessionId);
    await send("Page.bringToFront", {}, sessionId);
    let dropPoint;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const probe = await send(
        "Runtime.evaluate",
        {
          expression: `(() => {
            const manager = document.querySelector("extensions-manager");
            if (!manager || !chrome?.developerPrivate?.updateProfileConfiguration) {
              return null;
            }
            const bounds = manager.getBoundingClientRect();
            return {
              x: Math.max(1, Math.floor(bounds.left + bounds.width / 2)),
              y: Math.max(1, Math.floor(bounds.top + bounds.height / 2)),
            };
          })()`,
          returnByValue: true,
        },
        sessionId,
      );
      if (probe.result?.value?.x && probe.result?.value?.y) {
        dropPoint = probe.result.value;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!dropPoint) {
      throw new Error("Chrome extensions page did not expose its directory-drop surface");
    }
    const enabled = await send(
      "Runtime.evaluate",
      {
        expression: `(async () => {
          await chrome.developerPrivate.updateProfileConfiguration({
            inDeveloperMode: true,
          });
          const profile = await chrome.developerPrivate.getProfileConfiguration();
          return profile.inDeveloperMode === true;
        })()`,
        awaitPromise: true,
        returnByValue: true,
      },
      sessionId,
    );
    if (enabled.exceptionDetails || enabled.result?.value !== true) {
      throw new Error("Chrome did not enable developer mode for the disposable Profile");
    }
    const dragData = {
      items: [],
      files: [extension],
      dragOperationsMask: 1,
    };
    await send(
      "Input.dispatchDragEvent",
      {
        type: "dragEnter",
        x: dropPoint.x,
        y: dropPoint.y,
        data: dragData,
      },
      sessionId,
    );
    // Chrome's extensions page handles a directory drag in two explicit
    // developerPrivate calls: remember the current WebContents drop data on
    // dragenter, then load that remembered directory on drop. Invoke the same
    // calls explicitly so the hosted runner is independent of shadow-DOM event
    // routing while still exercising the persistent browser implementation.
    const loaded = await send(
      "Runtime.evaluate",
      {
        expression: `(async () => {
          try {
            chrome.developerPrivate.notifyDragInstallInProgress();
            const result = await chrome.developerPrivate.loadUnpacked({
              failQuietly: true,
              populateError: true,
              useDraggedPath: true,
            });
            return {ok: true, result: result ?? null};
          } catch (error) {
            return {
              ok: false,
              error: String(error?.stack || error),
              runtimeError: chrome.runtime?.lastError?.message || null,
            };
          }
        })()`,
        awaitPromise: true,
        returnByValue: true,
      },
      sessionId,
    );
    const loadResult = loaded.result?.value;
    process.stdout.write(`CHROME_DIRECTORY_DROP ${JSON.stringify(loadResult)}\n`);
    if (loaded.exceptionDetails || loadResult?.ok !== true || loadResult.result) {
      throw new Error(
        `Chrome directory-drop API rejected the approved extension: ${JSON.stringify(loadResult)}`,
      );
    }
    await send(
      "Input.dispatchDragEvent",
      {
        type: "dragCancel",
        x: dropPoint.x,
        y: dropPoint.y,
        data: dragData,
      },
      sessionId,
    );
    let lastExtensions = [];
    for (let attempt = 0; attempt < 300; attempt += 1) {
      const listed = await send("Extensions.getExtensions");
      lastExtensions = listed.extensions || [];
      const record = lastExtensions.find(
        (item) => item.id === args["expected-id"],
      );
      if (record) return record;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    const active = lastExtensions.map((item) => ({
      id: item.id,
      version: item.version,
      enabled: item.enabled,
      path: item.path,
    }));
    throw new Error(
      `Chrome directory drop did not load the approved unpacked extension; active=${JSON.stringify(active)}`,
    );
  }

  async function findExistingExtension() {
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const listed = await send("Extensions.getExtensions");
      const record = (listed.extensions || []).find(
        (item) => item.id === args["expected-id"],
      );
      if (record) return record;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("approved extension is not active in the restarted Chrome Profile");
  }

  async function seedExtensionAuthToken(extensionId, tokenFile) {
    const extensionUrl = `chrome-extension://${extensionId}/status.html`;
    const target = await send("Target.createTarget", { url: extensionUrl });
    const attached = await send("Target.attachToTarget", {
      targetId: target.targetId,
      flatten: true,
    });
    if (!attached.sessionId) {
      throw new Error("Chrome did not return an extension-page CDP session");
    }
    let currentUrl = "";
    for (let attempt = 0; attempt < 50; attempt += 1) {
      const location = await send(
        "Runtime.evaluate",
        { expression: "location.href", returnByValue: true },
        attached.sessionId,
      );
      currentUrl = location.result && location.result.value;
      if (typeof currentUrl === "string" && currentUrl.startsWith(extensionUrl)) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (typeof currentUrl !== "string" || !currentUrl.startsWith(extensionUrl)) {
      throw new Error(`extension status page did not load: ${currentUrl}`);
    }
    const token = crypto.randomBytes(32).toString("base64url");
    const seeded = await send(
      "Runtime.evaluate",
      {
        expression: `localStorage.setItem("auth-token", ${JSON.stringify(token)}); localStorage.getItem("auth-token")`,
        returnByValue: true,
      },
      attached.sessionId,
    );
    if (seeded.exceptionDetails || seeded.result?.value !== token) {
      throw new Error("could not seed the disposable extension authentication token");
    }
    fs.writeFileSync(tokenFile, token, { encoding: "utf8", mode: 0o600 });
    await send("Target.closeTarget", { targetId: target.targetId });
  }

  try {
    const record =
      args.mode === "install-drag"
        ? await loadUnpackedThroughChromeDrop()
        : await findExistingExtension();
    if (record.enabled !== true || record.version !== args["expected-version"]) {
      throw new Error(
        `loaded extension is not enabled at the approved version: ${JSON.stringify(record)}`,
      );
    }
    if (
      args.mode === "install-drag" &&
      path.resolve(record.path).toLowerCase() !== extension.toLowerCase()
    ) {
      throw new Error(`loaded extension path mismatch: ${record.path} != ${extension}`);
    }
    await send("Storage.setCookies", {
      cookies: [
        {
          name: SESSION_COOKIE_NAME,
          value: SESSION_COOKIE_VALUE,
          url: "http://127.0.0.1/",
          path: "/",
          httpOnly: true,
          secure: false,
          sameSite: "Lax",
          expires: Math.floor(Date.now() / 1000) + 3600,
        },
      ],
    });
    await seedExtensionAuthToken(record.id, path.resolve(args["token-file"]));
    process.stdout.write(
      `${JSON.stringify({ id: record.id, version: record.version, enabled: record.enabled })}\n`,
    );
    await send("Browser.close");
    const exitCode = await waitForExit(child, 15000);
    if (exitCode !== 0 && exitCode !== null) {
      throw new Error(`Chrome exited with code ${exitCode}; stderr=${stderr}`);
    }
  } catch (error) {
    child.kill();
    throw error;
  }
}

main().catch((error) => {
  process.stderr.write(`ERROR: ${error.stack || error}\n`);
  // Chrome descendants can retain remote-debugging-pipe handles after the
  // browser process is killed. A failed CI probe must not keep the Node event
  // loop alive and strand the still-waiting one-click installer.
  process.exit(1);
});
