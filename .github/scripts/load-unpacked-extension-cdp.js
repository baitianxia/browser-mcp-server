#!/usr/bin/env node
"use strict";

// CI-only Chrome probe. Chrome 137+ removed --load-extension from branded
// builds. Drive Chrome's own "Load unpacked" UI over a pipe-only DevTools
// connection so the result has the same restart persistence as the documented
// manual fallback.

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn, spawnSync } = require("child_process");

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
  ]) {
    if (!result[name]) {
      throw new Error(`missing --${name}`);
    }
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

function enableDeveloperMode(profile) {
  const defaultProfile = path.join(profile, "Default");
  const preferencesPath = path.join(defaultProfile, "Preferences");
  fs.mkdirSync(defaultProfile, { recursive: true });
  let preferences = {};
  if (fs.existsSync(preferencesPath)) {
    try {
      preferences = JSON.parse(fs.readFileSync(preferencesPath, "utf8"));
    } catch (error) {
      throw new Error(`cannot read disposable Chrome Preferences: ${error}`);
    }
  }
  if (!preferences || typeof preferences !== "object" || Array.isArray(preferences)) {
    throw new Error("disposable Chrome Preferences root is not an object");
  }
  if (!preferences.extensions || typeof preferences.extensions !== "object") {
    preferences.extensions = {};
  }
  if (
    !preferences.extensions.ui ||
    typeof preferences.extensions.ui !== "object"
  ) {
    preferences.extensions.ui = {};
  }
  // This is the exact browser preference changed by the user's first manual
  // step and makes Chrome's own Load unpacked button available to the probe.
  preferences.extensions.ui.developer_mode = true;
  const temporary = `${preferencesPath}.tmp-${process.pid}`;
  fs.writeFileSync(temporary, `${JSON.stringify(preferences)}\n`, "utf8");
  fs.renameSync(temporary, preferencesPath);
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
  enableDeveloperMode(profile);

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
      windowsHide: true,
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

  function selectExtensionFolder() {
    if (process.platform !== "win32") {
      throw new Error(
        `the native folder-dialog probe is Windows-only; platform=${process.platform}`,
      );
    }
    const environment = {
      ...process.env,
      INTRANET_EXTENSION_DIR: extension,
      INTRANET_CHROME_PID: String(child.pid),
    };
    const script = [
      "Add-Type -AssemblyName System.Windows.Forms",
      "$shell = New-Object -ComObject WScript.Shell",
      "$null = $shell.AppActivate([int]$env:INTRANET_CHROME_PID)",
      "Start-Sleep -Milliseconds 700",
      "[System.Windows.Forms.Clipboard]::SetText($env:INTRANET_EXTENSION_DIR)",
      "[System.Windows.Forms.SendKeys]::SendWait('^l')",
      "Start-Sleep -Milliseconds 300",
      "[System.Windows.Forms.SendKeys]::SendWait('^v')",
      "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')",
      "Start-Sleep -Milliseconds 700",
      "[System.Windows.Forms.SendKeys]::SendWait('%s')",
      "Start-Sleep -Milliseconds 300",
      "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')",
    ].join("; ");
    const selection = spawnSync(
      "powershell.exe",
      ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
      { env: environment, encoding: "utf8", windowsHide: false },
    );
    if (selection.error || selection.status !== 0) {
      throw new Error(
        `could not select the unpacked extension in Chrome's folder dialog: ` +
          `${selection.error || selection.stderr || `exit=${selection.status}`}`,
      );
    }
  }

  async function loadUnpackedThroughChromeUi() {
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
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const probe = await send(
        "Runtime.evaluate",
        {
          expression: `(() => {
            const manager = document.querySelector("extensions-manager");
            const toolbar = manager?.shadowRoot?.querySelector("extensions-toolbar");
            const button = toolbar?.shadowRoot?.querySelector("#loadUnpacked");
            return Boolean(button && !button.disabled);
          })()`,
          returnByValue: true,
        },
        sessionId,
      );
      if (probe.result?.value === true) {
        ready = true;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!ready) {
      throw new Error('Chrome extensions page did not expose an enabled "Load unpacked" button');
    }
    const clicked = await send(
      "Runtime.evaluate",
      {
        expression: `(() => {
          const manager = document.querySelector("extensions-manager");
          const toolbar = manager.shadowRoot.querySelector("extensions-toolbar");
          toolbar.shadowRoot.querySelector("#loadUnpacked").click();
          return true;
        })()`,
        returnByValue: true,
      },
      sessionId,
    );
    if (clicked.exceptionDetails || clicked.result?.value !== true) {
      throw new Error("could not click Chrome's Load unpacked button");
    }
    selectExtensionFolder();
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const listed = await send("Extensions.getExtensions");
      const record = (listed.extensions || []).find(
        (item) => item.id === args["expected-id"],
      );
      if (record) return record;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("Chrome UI did not load the approved unpacked extension");
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
    const record = await loadUnpackedThroughChromeUi();
    if (record.enabled !== true || record.version !== args["expected-version"]) {
      throw new Error(
        `loaded extension is not enabled at the approved version: ${JSON.stringify(record)}`,
      );
    }
    if (path.resolve(record.path).toLowerCase() !== extension.toLowerCase()) {
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
  process.exitCode = 1;
});
