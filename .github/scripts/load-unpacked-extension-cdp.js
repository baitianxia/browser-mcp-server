#!/usr/bin/env node
"use strict";

// CI-only session surrogate for Chrome's human-only "Load unpacked" boundary.
// GitHub-hosted Windows runners cannot provide trusted input to Chrome's native
// folder picker. This probe therefore uses the pipe-only DevTools command to
// load the exact approved directory for one live browser session. It keeps that
// browser alive while the same installer process detects the extension and
// continues, and while the installed MCP performs an authenticated offline E2E.
// This is deliberately not evidence that the manual load survives a restart.

const fs = require("fs");
const path = require("path");
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
    "ready-file",
    "stop-file",
    "maximum-seconds",
  ]) {
    if (!result[name]) {
      throw new Error(`missing --${name}`);
    }
  }
  const maximumSeconds = Number(result["maximum-seconds"]);
  if (!Number.isInteger(maximumSeconds) || maximumSeconds < 30) {
    throw new Error("--maximum-seconds must be an integer of at least 30");
  }
  result.maximumSeconds = maximumSeconds;
  return result;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
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

function normalizedPath(value) {
  return path.resolve(value).toLowerCase();
}

function writeAtomic(file, contents, options = {}) {
  const temporary = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(temporary, contents, options);
  fs.renameSync(temporary, file);
}

async function waitForStop(stopFile, child, maximumSeconds, stderrText) {
  const deadline = Date.now() + maximumSeconds * 1000;
  while (Date.now() < deadline) {
    if (fs.existsSync(stopFile)) return;
    if (child.exitCode !== null) {
      throw new Error(
        `Chrome exited before the CI E2E completed; code=${child.exitCode}; ` +
          `stderr=${stderrText()}`,
      );
    }
    await delay(250);
  }
  throw new Error(`CI did not create the stop marker within ${maximumSeconds} seconds`);
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  const chrome = path.resolve(args.chrome);
  const profile = path.resolve(args.profile);
  const extension = path.resolve(args.extension);
  const tokenFile = path.resolve(args["token-file"]);
  const readyFile = path.resolve(args["ready-file"]);
  const stopFile = path.resolve(args["stop-file"]);
  if (!fs.statSync(chrome).isFile()) {
    throw new Error(`Chrome executable is missing: ${chrome}`);
  }
  if (!fs.statSync(path.join(extension, "manifest.json")).isFile()) {
    throw new Error(`unpacked extension is missing manifest.json: ${extension}`);
  }
  for (const marker of [tokenFile, readyFile, stopFile]) {
    if (fs.existsSync(marker)) {
      throw new Error(`refusing a stale CI marker: ${marker}`);
    }
    fs.mkdirSync(path.dirname(marker), { recursive: true });
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
      if (separator < 0) break;
      const payload = buffer.subarray(0, separator).toString("utf8");
      buffer = buffer.subarray(separator + 1);
      if (!payload) continue;
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
      if (sessionId) message.sessionId = sessionId;
      protocolInput.write(`${JSON.stringify(message)}\0`, "utf8");
    });
  }

  async function readExtensionAuthToken(extensionId) {
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
      if (typeof currentUrl === "string" && currentUrl.startsWith(extensionUrl)) break;
      await delay(100);
    }
    if (typeof currentUrl !== "string" || !currentUrl.startsWith(extensionUrl)) {
      throw new Error(`extension status page did not load: ${currentUrl}`);
    }
    // Read the token rendered by the extension itself. The background service
    // caches this value during startup, so overwriting localStorage after the
    // extension has initialized can produce a token that looks persisted but
    // is not accepted by the running service worker. This mirrors Playwright's
    // own extension fixture, which reads `.auth-token-code` from status.html.
    const tokenPrefix = "PLAYWRIGHT_MCP_EXTENSION_TOKEN=";
    let token = "";
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const rendered = await send(
        "Runtime.evaluate",
        {
          expression:
            'document.querySelector(".auth-token-code")?.textContent || ""',
          returnByValue: true,
        },
        attached.sessionId,
      );
      const text = rendered.result?.value;
      if (typeof text === "string" && text.trim().startsWith(tokenPrefix)) {
        token = text.trim().slice(tokenPrefix.length);
        if (token.length >= 32 && !/\s/.test(token)) break;
        token = "";
      }
      await delay(100);
    }
    if (!token) {
      throw new Error("extension status page did not expose a valid authentication token");
    }
    writeAtomic(tokenFile, token, { encoding: "utf8", mode: 0o600 });
    await send("Target.closeTarget", { targetId: target.targetId });
  }

  try {
    let listed = await send("Extensions.getExtensions");
    let record = (listed.extensions || []).find(
      (item) => item.id === args["expected-id"],
    );
    let loadedForSession = false;
    if (!record) {
      const loaded = await send("Extensions.loadUnpacked", {
        path: extension,
        enableInIncognito: false,
      });
      loadedForSession = true;
      if (loaded.id !== args["expected-id"]) {
        throw new Error(
          `loaded extension ID mismatch: ${loaded.id} != ${args["expected-id"]}`,
        );
      }
      listed = await send("Extensions.getExtensions");
      record = (listed.extensions || []).find(
        (item) => item.id === args["expected-id"],
      );
    }
    if (!record) {
      throw new Error("loaded extension is absent from Extensions.getExtensions");
    }
    if (record.enabled !== true || record.version !== args["expected-version"]) {
      throw new Error(
        `loaded extension is not enabled at the approved version: ${JSON.stringify(record)}`,
      );
    }
    if (loadedForSession && normalizedPath(record.path) !== normalizedPath(extension)) {
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
    await readExtensionAuthToken(record.id);
    const ready = {
      sessionOnly: loadedForSession,
      source: loadedForSession ? "session-surrogate" : "existing-installation",
      id: record.id,
      version: record.version,
      enabled: record.enabled,
      path: path.resolve(record.path),
    };
    writeAtomic(readyFile, `${JSON.stringify(ready)}\n`, "utf8");
    process.stdout.write(`CI_SESSION_ONLY_EXTENSION_READY ${JSON.stringify(ready)}\n`);

    await waitForStop(stopFile, child, args.maximumSeconds, () => stderr);
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
  // A failed helper must never strand the real one-click installer at its
  // intentionally unbounded manual wait.
  process.exit(1);
});
