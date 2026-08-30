#!/usr/bin/env node
"use strict";

// CI-only Chrome probe. Chrome 137+ removed --load-extension from branded
// builds, so use the supported pipe-only Extensions.loadUnpacked CDP command.

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

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

  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = nextId++;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`Chrome protocol ${method} timed out; stderr=${stderr}`));
      }, 45000);
      pending.set(id, { method, resolve, reject, timer });
      protocolInput.write(`${JSON.stringify({ id, method, params })}\0`, "utf8");
    });
  }

  try {
    const loaded = await send("Extensions.loadUnpacked", {
      path: extension,
      enableInIncognito: false,
    });
    if (loaded.id !== args["expected-id"]) {
      throw new Error(
        `loaded extension ID mismatch: ${loaded.id} != ${args["expected-id"]}`,
      );
    }
    const listed = await send("Extensions.getExtensions");
    const record = (listed.extensions || []).find(
      (item) => item.id === args["expected-id"],
    );
    if (!record) {
      throw new Error("loaded extension is absent from Extensions.getExtensions");
    }
    if (record.enabled !== true || record.version !== args["expected-version"]) {
      throw new Error(
        `loaded extension is not enabled at the approved version: ${JSON.stringify(record)}`,
      );
    }
    if (path.resolve(record.path).toLowerCase() !== extension.toLowerCase()) {
      throw new Error(`loaded extension path mismatch: ${record.path} != ${extension}`);
    }
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
