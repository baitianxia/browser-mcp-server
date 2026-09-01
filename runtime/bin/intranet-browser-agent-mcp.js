#!/usr/bin/env node
"use strict";

// Offline stdio compatibility layer for the pinned @playwright/mcp runtime.
// It deliberately uses Node.js built-ins only so the target host never needs a
// package-manager repair step.

const fs = require("fs");
const path = require("path");
const readline = require("readline");
const { spawn } = require("child_process");

const INTERNAL_ID_PREFIX = `intranet-${process.pid}-${Date.now()}-`;
const MAX_COMPACT_SNAPSHOT_CHARS = 16000;
const ACTION_TOOLS_WITH_SNAPSHOT = new Set([
  "browser_click",
  "browser_drag",
  "browser_drop",
  "browser_fill_form",
  "browser_handle_dialog",
  "browser_hover",
  "browser_navigate",
  "browser_navigate_back",
  "browser_navigate_forward",
  "browser_press_key",
  "browser_reload",
  "browser_select_option",
  "browser_tabs",
  "browser_type",
  "browser_wait_for",
]);
const ACTIONABILITY_FAILURE = /(?:timed?\s*out|timeout|not\s+(?:visible|stable|enabled)|outside\s+(?:of\s+)?the\s+viewport|intercepts?\s+pointer|detached|not\s+attached|element\s+is\s+not\s+visible|waiting\s+for\s+element\s+to\s+be\s+visible)/i;
const AMBIGUOUS_TARGET_FAILURE = /(?:strict\s+mode\s+violation|resolved\s+to\s+\d+\s+elements?|matched\s+\d+\s+elements?|multiple\s+elements?)/i;

function failStartup(message) {
  process.stderr.write(`Intranet Browser Agent MCP: ${message}\n`);
  process.exitCode = 2;
}

function configPathFromArgs(args) {
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--config" && index + 1 < args.length)
      return path.resolve(args[index + 1]);
    if (args[index].startsWith("--config="))
      return path.resolve(args[index].slice("--config=".length));
  }
  return undefined;
}

function loadInteractionConfig(args) {
  const defaults = {
    schemaVersion: 1,
    snapshotStrategy: "full",
    compatibilityMode: "standard",
    defaultSnapshotDepth: 6,
    settleMs: 1500,
  };
  const playwrightConfig = configPathFromArgs(args);
  if (!playwrightConfig)
    return defaults;
  const interactionPath = path.join(path.dirname(playwrightConfig), "interaction.config.json");
  if (!fs.existsSync(interactionPath))
    throw new Error(`interaction config is missing beside Playwright config: ${interactionPath}`);
  let value;
  try {
    value = JSON.parse(fs.readFileSync(interactionPath, "utf8"));
  } catch (error) {
    throw new Error(`cannot parse ${interactionPath}: ${error.message}`);
  }
  const exactKeys = [
    "compatibilityMode",
    "defaultSnapshotDepth",
    "schemaVersion",
    "settleMs",
    "snapshotStrategy",
  ];
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(exactKeys))
    throw new Error(`invalid interaction config shape: ${interactionPath}`);
  if (value.schemaVersion !== 1)
    throw new Error(`unsupported interaction config schema: ${interactionPath}`);
  if (!["compact", "full"].includes(value.snapshotStrategy))
    throw new Error(`invalid snapshotStrategy in ${interactionPath}`);
  if (!["robust", "standard"].includes(value.compatibilityMode))
    throw new Error(`invalid compatibilityMode in ${interactionPath}`);
  if (!Number.isInteger(value.defaultSnapshotDepth) || value.defaultSnapshotDepth < 1 || value.defaultSnapshotDepth > 20)
    throw new Error(`invalid defaultSnapshotDepth in ${interactionPath}`);
  if (!Number.isInteger(value.settleMs) || value.settleMs < 100 || value.settleMs > 10000)
    throw new Error(`invalid settleMs in ${interactionPath}`);
  return value;
}

function writeMessage(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function textResult(message, isError = false) {
  return { content: [{ type: "text", text: String(message) }], ...(isError ? { isError: true } : {}) };
}

function textFromResult(result) {
  if (!result || !Array.isArray(result.content))
    return "";
  return result.content
    .filter(item => item && item.type === "text" && typeof item.text === "string")
    .map(item => item.text)
    .join("\n");
}

function evaluationValue(result) {
  const text = textFromResult(result);
  const fenced = /```json\s*([\s\S]*?)```/i.exec(text);
  const candidates = [];
  if (fenced)
    candidates.push(fenced[1].trim());
  candidates.push(text.trim());
  const firstObject = text.indexOf("{");
  const lastObject = text.lastIndexOf("}");
  if (firstObject !== -1 && lastObject > firstObject)
    candidates.push(text.slice(firstObject, lastObject + 1));
  const firstArray = text.indexOf("[");
  const lastArray = text.lastIndexOf("]");
  if (firstArray !== -1 && lastArray > firstArray)
    candidates.push(text.slice(firstArray, lastArray + 1));
  for (const candidate of candidates) {
    try {
      return JSON.parse(candidate);
    } catch {
      // Continue to the next representation.
    }
  }
  return undefined;
}

function hasContent(result) {
  return Boolean(result && Array.isArray(result.content) && result.content.some(item => {
    if (!item || typeof item !== "object")
      return false;
    if (item.type === "text")
      return typeof item.text === "string" && item.text.trim().length > 0;
    return true;
  }));
}

function boundCompactSnapshot(result) {
  if (!result || !Array.isArray(result.content) || result.isError === true)
    return result;
  let remaining = MAX_COMPACT_SNAPSHOT_CHARS;
  let truncated = false;
  const content = [];
  for (const item of result.content) {
    if (!item || item.type !== "text" || typeof item.text !== "string") {
      content.push(item);
      continue;
    }
    if (remaining <= 0) {
      truncated = true;
      continue;
    }
    if (item.text.length <= remaining) {
      content.push(item);
      remaining -= item.text.length;
      continue;
    }
    let end = remaining;
    const lineEnd = item.text.lastIndexOf("\n", remaining);
    if (lineEnd >= Math.floor(remaining * 0.75))
      end = lineEnd;
    content.push({ ...item, text: item.text.slice(0, end) });
    remaining = 0;
    truncated = true;
  }
  if (truncated) {
    content.push({
      type: "text",
      text: "Compact snapshot truncated at 16000 characters. Use browser_find, a targeted browser_snapshot, or switch to full snapshots when deeper content is required.",
    });
  }
  return { ...result, content };
}

function mergeResults(primary, snapshot, note) {
  const first = primary && Array.isArray(primary.content) ? primary.content : [];
  const second = snapshot && Array.isArray(snapshot.content) ? snapshot.content : [];
  return {
    ...(primary && typeof primary === "object" ? primary : {}),
    content: [
      ...first,
      ...(note ? [{ type: "text", text: note }] : []),
      ...second,
    ],
  };
}

function boundedInteger(value, fallback, minimum, maximum) {
  if (value === undefined)
    return fallback;
  if (!Number.isInteger(value) || value < minimum || value > maximum)
    throw new Error(`expected an integer from ${minimum} to ${maximum}`);
  return value;
}

function requiredString(args, key) {
  const value = args && args[key];
  if (typeof value !== "string" || !value.trim())
    throw new Error(`${key} must be a non-empty string`);
  return value;
}

function assertOnlyKeys(args, allowed) {
  const unknown = Object.keys(args).filter(key => !allowed.has(key));
  if (unknown.length)
    throw new Error(`unsupported argument(s): ${unknown.sort().join(", ")}`);
}

function waitOptions(args, defaultTimeout) {
  const options = {};
  for (const key of ["waitForText", "waitForTextGone", "waitForSelector", "waitForUrl"]) {
    if (args[key] !== undefined) {
      if (typeof args[key] !== "string" || !args[key])
        throw new Error(`${key} must be a non-empty string`);
      options[key] = args[key];
    }
  }
  options.timeoutMs = boundedInteger(args.timeoutMs, defaultTimeout, 100, 120000);
  return options;
}

function stripWaitOptions(args) {
  const result = { ...args };
  for (const key of ["waitForText", "waitForTextGone", "waitForSelector", "waitForUrl", "timeoutMs"])
    delete result[key];
  return result;
}

function customTools() {
  const target = {
    type: "string",
    description: "Exact target element reference from the latest page snapshot, or a unique element selector",
  };
  const element = {
    type: "string",
    description: "Human-readable element description used for interaction permission",
  };
  const timeout = {
    type: "integer",
    minimum: 100,
    maximum: 120000,
    description: "Maximum time to verify the requested page state",
  };
  const waits = {
    waitForText: { type: "string", minLength: 1, description: "Wait until this visible page text appears" },
    waitForTextGone: { type: "string", minLength: 1, description: "Wait until this page text disappears" },
    waitForSelector: { type: "string", minLength: 1, description: "Wait until this CSS selector exists and is visible" },
    waitForUrl: { type: "string", minLength: 1, description: "Wait until the current URL contains this value" },
    timeoutMs: timeout,
  };
  return [
    {
      name: "browser_select_custom_option",
      title: "Select custom dropdown option",
      description: "Open a readonly/custom combobox (including Element UI) and click one visible option by text",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["target", "optionText"],
        properties: {
          target,
          element,
          optionText: { type: "string", minLength: 1, description: "Visible option text to select" },
          exact: { type: "boolean", description: "Require exact normalized text; defaults to true" },
          timeoutMs: timeout,
        },
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    {
      name: "browser_read_tooltip",
      title: "Read tooltip",
      description: "Return untruncated tooltip text from ARIA/title metadata or a triggered visible tooltip",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["target"],
        properties: { target, element, timeoutMs: timeout },
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    {
      name: "browser_click_and_wait",
      title: "Click and verify page state",
      description: "Click with the configured compatibility policy, wait for an explicit page condition (or DOM stability), then return a fresh snapshot using the configured strategy",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["target"],
        properties: {
          target,
          element,
          doubleClick: { type: "boolean" },
          button: { enum: ["left", "right", "middle"] },
          modifiers: { type: "array", items: { enum: ["Alt", "Control", "ControlOrMeta", "Meta", "Shift"] } },
          ...waits,
        },
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
  ];
}

function enhanceToolList(result, config) {
  if (!result || !Array.isArray(result.tools))
    return result;
  const tools = result.tools.map(tool => {
    if (!tool || typeof tool !== "object")
      return tool;
    const copy = { ...tool };
    if (tool.name === "browser_snapshot" && config.snapshotStrategy === "compact")
      copy.description = `${tool.description || "Capture page snapshot"}. Defaults to an inline depth-${config.defaultSnapshotDepth} compact snapshot; set filename for an external full artifact.`;
    if (tool.name === "browser_click" && config.compatibilityMode === "robust")
      copy.description = `${tool.description || "Click an element"}. Uses native Playwright actionability first, then a guarded same-target DOM click only for visibility/stability failures.`;
    if (tool.name === "browser_type" && config.compatibilityMode === "robust")
      copy.description = `${tool.description || "Type text"}. Readonly custom comboboxes are selected by visible option text; readonly ordinary fields fail immediately.`;
    if (tool.name === "browser_navigate") {
      copy.inputSchema = JSON.parse(JSON.stringify(tool.inputSchema || { type: "object", properties: {} }));
      copy.inputSchema.properties = copy.inputSchema.properties || {};
      Object.assign(copy.inputSchema.properties, {
        waitForText: { type: "string", minLength: 1, description: "Optional text that must appear before the returned snapshot" },
        waitForTextGone: { type: "string", minLength: 1, description: "Optional text that must disappear before the returned snapshot" },
        waitForSelector: { type: "string", minLength: 1, description: "Optional visible CSS selector required before the returned snapshot" },
        waitForUrl: { type: "string", minLength: 1, description: "Optional substring required in the final URL" },
        timeoutMs: { type: "integer", minimum: 100, maximum: 120000, description: "Readiness timeout" },
      });
      if (config.compatibilityMode === "robust")
        copy.description = `${tool.description || "Navigate to a URL"}. Waits for explicit readiness fields or DOM stability and returns a fresh snapshot using the configured strategy.`;
    }
    return copy;
  });
  const names = new Set(tools.map(tool => tool && tool.name));
  for (const tool of customTools()) {
    if (!names.has(tool.name))
      tools.push(tool);
  }
  return { ...result, tools };
}

function runProxy(upstreamCli, upstreamArgs, config) {
  const child = spawn(process.execPath, [upstreamCli, ...upstreamArgs], {
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
    env: process.env,
  });
  let internalSequence = 0;
  let childClosed = false;
  const pending = new Map();
  let actionQueue = Promise.resolve();

  child.stderr.on("data", data => process.stderr.write(data));
  child.on("error", error => {
    failStartup(`cannot start pinned Playwright MCP: ${error.message}`);
  });
  child.on("exit", (code, signal) => {
    childClosed = true;
    const reason = new Error(`pinned Playwright MCP exited (code=${code}, signal=${signal || "none"})`);
    for (const waiter of pending.values())
      waiter.reject(reason);
    pending.clear();
    if (process.exitCode === undefined)
      process.exitCode = code === 0 ? 0 : (code || 2);
  });

  const upstreamLines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  upstreamLines.on("line", line => {
    if (!line.trim())
      return;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      process.stderr.write(`Intranet Browser Agent MCP: invalid upstream JSON-RPC output: ${line.slice(0, 500)}\n`);
      child.kill();
      return;
    }
    if (message && typeof message.id === "string" && message.id.startsWith(INTERNAL_ID_PREFIX) && pending.has(message.id)) {
      const waiter = pending.get(message.id);
      pending.delete(message.id);
      waiter.resolve(message);
      return;
    }
    writeMessage(message);
  });

  function sendUpstream(message) {
    if (childClosed || !child.stdin.writable)
      throw new Error("pinned Playwright MCP is not running");
    child.stdin.write(`${JSON.stringify(message)}\n`);
  }

  function requestUpstream(method, params) {
    const id = `${INTERNAL_ID_PREFIX}${++internalSequence}`;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      try {
        sendUpstream({ jsonrpc: "2.0", id, method, params: params || {} });
      } catch (error) {
        pending.delete(id);
        reject(error);
      }
    });
  }

  async function upstreamResult(method, params) {
    const response = await requestUpstream(method, params);
    if (response.error) {
      const error = new Error(response.error.message || `upstream ${method} failed`);
      error.rpcError = response.error;
      throw error;
    }
    return response.result;
  }

  async function callTool(name, args) {
    return upstreamResult("tools/call", { name, arguments: args || {} });
  }

  async function waitForDomQuiet() {
    const quietMs = config.settleMs;
    const maximumMs = Math.min(30000, Math.max(quietMs * 4, quietMs + 1000));
    const expression = `() => new Promise(resolve => {
      const quietMs = ${quietMs};
      const maximumMs = ${maximumMs};
      const started = Date.now();
      let changed = Date.now();
      let observer;
      const finish = reason => {
        if (observer) observer.disconnect();
        clearInterval(interval);
        resolve({ reason, elapsedMs: Date.now() - started, readyState: document.readyState });
      };
      if (!document.documentElement) { resolve({ reason: "no-document-element", elapsedMs: 0 }); return; }
      observer = new MutationObserver(() => { changed = Date.now(); });
      observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
      const interval = setInterval(() => {
        const now = Date.now();
        if (document.readyState !== "loading" && now - changed >= quietMs) finish("settled");
        else if (now - started >= maximumMs) finish("maximum-wait");
      }, Math.min(100, quietMs));
    })`;
    return callTool("browser_evaluate", { function: expression });
  }

  async function waitForConditions(options) {
    const hasCondition = Boolean(options.waitForText || options.waitForTextGone || options.waitForSelector || options.waitForUrl);
    if (!hasCondition)
      return waitForDomQuiet();
    const safe = JSON.stringify(options);
    const expression = `() => new Promise((resolve, reject) => {
      const options = ${safe};
      const started = Date.now();
      const visible = element => {
        if (!element || !element.isConnected) return false;
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
      };
      const check = () => {
        const bodyText = document.body ? (document.body.innerText || document.body.textContent || "") : "";
        let selectorMatch = true;
        if (options.waitForSelector) {
          try { selectorMatch = visible(document.querySelector(options.waitForSelector)); }
          catch (error) { reject(new Error("invalid waitForSelector: " + error.message)); return; }
        }
        const met = (!options.waitForText || bodyText.includes(options.waitForText)) &&
          (!options.waitForTextGone || !bodyText.includes(options.waitForTextGone)) &&
          selectorMatch && (!options.waitForUrl || location.href.includes(options.waitForUrl));
        if (met) { resolve({ met: true, elapsedMs: Date.now() - started, url: location.href }); return; }
        if (Date.now() - started >= options.timeoutMs) {
          reject(new Error("page condition was not met before timeout"));
          return;
        }
        setTimeout(check, 50);
      };
      check();
    })`;
    return callTool("browser_evaluate", { function: expression });
  }

  async function compactSnapshot(snapshotArguments = {}) {
    let last = textResult("compact snapshot was not attempted", true);
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const request = { ...snapshotArguments };
      if (request.depth === undefined)
        request.depth = config.defaultSnapshotDepth;
      if (request.boxes === undefined)
        request.boxes = false;
      last = await callTool("browser_snapshot", request);
      if (!last || last.isError !== true) {
        if (hasContent(last))
          return boundCompactSnapshot(last);
      }
      if (attempt < 2)
        await waitForDomQuiet();
    }
    return last && last.isError === true
      ? last
      : textResult("The page returned an empty compact snapshot after three in-call attempts.", true);
  }

  async function appendFreshSnapshot(result, note, requireFreshFull = false) {
    if (!result || result.isError === true)
      return result;
    let snapshot;
    if (config.snapshotStrategy === "compact")
      snapshot = await compactSnapshot();
    else if (requireFreshFull)
      snapshot = await callTool("browser_snapshot", {});
    else
      return result;
    if (snapshot && snapshot.isError === true) {
      return mergeResults(result, undefined,
        "Action completed, but re-observation failed. Do not repeat the action blindly; request browser_snapshot or inspect page state first.");
    }
    return mergeResults(result, snapshot, note || "Fresh compact page snapshot:");
  }

  async function guardedClick(args) {
    const native = await callTool("browser_click", args);
    if (!native || native.isError !== true)
      return { result: native, fallback: false };
    const detail = textFromResult(native);
    const unsupportedGesture = args.doubleClick === true || (args.button && args.button !== "left") ||
      (Array.isArray(args.modifiers) && args.modifiers.length > 0);
    if (config.compatibilityMode !== "robust" || unsupportedGesture ||
        AMBIGUOUS_TARGET_FAILURE.test(detail) || !ACTIONABILITY_FAILURE.test(detail))
      return { result: native, fallback: false };
    const fallback = await callTool("browser_evaluate", {
      element: args.element,
      target: args.target,
      function: `(element) => {
        if (!(element instanceof Element) || !element.isConnected) throw new Error("same target is detached");
        const disabled = element.matches(":disabled") || element.getAttribute("aria-disabled") === "true" || element.classList.contains("is-disabled");
        if (disabled) throw new Error("same target is disabled");
        element.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0 || rect.width <= 0 || rect.height <= 0)
          throw new Error("same target is not visible after scrollIntoView");
        if (typeof element.click !== "function") throw new Error("same target has no DOM click method");
        element.click();
        return { clicked: true, fallback: "guarded-same-target-dom-click", tag: element.tagName, text: (element.innerText || element.textContent || "").trim().slice(0, 200) };
      }`,
    });
    if (fallback && fallback.isError === true)
      return { result: native, fallback: false };
    return {
      result: mergeResults(fallback, undefined,
        "Native Playwright click could not satisfy actionability checks; a guarded DOM click was applied to the exact same target."),
      fallback: true,
    };
  }

  async function selectCustomOption(args) {
    assertOnlyKeys(args, new Set(["element", "exact", "optionText", "target", "timeoutMs"]));
    const target = requiredString(args, "target");
    const optionText = requiredString(args, "optionText");
    if (args.exact !== undefined && typeof args.exact !== "boolean")
      throw new Error("exact must be a boolean");
    const timeoutMs = boundedInteger(args.timeoutMs, 5000, 100, 120000);
    const exact = args.exact !== false;
    const expression = `(element) => new Promise((resolve, reject) => {
      const wanted = ${JSON.stringify(optionText)}.replace(/\\s+/g, " ").trim();
      const exact = ${exact ? "true" : "false"};
      const timeoutMs = ${timeoutMs};
      const started = Date.now();
      const field = element.matches("input,[role=combobox],.el-select") ? element : element.querySelector("input,[role=combobox],.el-select") || element;
      const container = field.closest(".el-select,.el-cascader,[role=combobox]") || field;
      if (!container.isConnected) { reject(new Error("custom select target is detached")); return; }
      if (container.matches(":disabled") || container.getAttribute("aria-disabled") === "true" || container.classList.contains("is-disabled")) {
        reject(new Error("custom select target is disabled")); return;
      }
      container.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
      if (typeof field.click === "function") field.click(); else container.click();
      const visible = option => {
        const style = getComputedStyle(option);
        const rect = option.getBoundingClientRect();
        return option.isConnected && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0 &&
          option.getAttribute("aria-disabled") !== "true" && !option.classList.contains("is-disabled");
      };
      const poll = () => {
        const options = Array.from(document.querySelectorAll('[role="option"],.el-select-dropdown__item,.el-cascader-node,.el-dropdown-menu__item')).filter(visible);
        const matches = options.filter(option => {
          const text = (option.innerText || option.textContent || "").replace(/\\s+/g, " ").trim();
          return exact ? text === wanted : text.includes(wanted);
        });
        if (matches.length === 1) {
          const option = matches[0];
          option.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "auto" });
          option.click();
          resolve({ selected: wanted, exact, tag: option.tagName });
          return;
        }
        if (matches.length > 1) { reject(new Error("custom option text matched more than one visible option")); return; }
        if (Date.now() - started >= timeoutMs) { reject(new Error("custom option did not become visible before timeout")); return; }
        setTimeout(poll, 50);
      };
      poll();
    })`;
    return callTool("browser_evaluate", {
      element: args.element,
      target,
      function: expression,
    });
  }

  async function inspectField(args) {
    return callTool("browser_evaluate", {
      element: args.element,
      target: args.target,
      function: `(element) => {
        const field = element.matches("input,textarea,select,[contenteditable],[role=combobox]") ? element :
          element.querySelector("input,textarea,select,[contenteditable],[role=combobox]") || element;
        const tag = field.tagName.toLowerCase();
        const role = field.getAttribute("role") || element.getAttribute("role") || "";
        const readonly = field.hasAttribute("readonly") || field.getAttribute("aria-readonly") === "true";
        const disabled = field.matches(":disabled") || field.getAttribute("aria-disabled") === "true" || field.classList.contains("is-disabled");
        const customSelect = Boolean(field.closest(".el-select,.el-cascader,[role=combobox]") || role === "combobox");
        const editable = !readonly && !disabled && (tag === "input" || tag === "textarea" || tag === "select" || field.isContentEditable);
        return { tag, role, readonly, disabled, customSelect, editable };
      }`,
    });
  }

  async function readTooltip(args) {
    assertOnlyKeys(args, new Set(["element", "target", "timeoutMs"]));
    const target = requiredString(args, "target");
    const timeoutMs = boundedInteger(args.timeoutMs, 3000, 100, 120000);
    const expression = `(element) => new Promise((resolve, reject) => {
      const normalize = value => (value || "").replace(/\\s+/g, " ").trim();
      const described = normalize((element.getAttribute("aria-describedby") || "").split(/\\s+/).map(id => {
        const node = document.getElementById(id); return node ? node.textContent : "";
      }).join(" "));
      const direct = described || normalize(element.getAttribute("aria-description")) || normalize(element.getAttribute("title")) ||
        normalize(element.getAttribute("data-tooltip")) || normalize(element.getAttribute("data-original-title")) || normalize(element.getAttribute("aria-label"));
      if (direct) { resolve({ text: direct, source: "attribute" }); return; }
      for (const type of ["pointerenter", "mouseenter", "mouseover"]) {
        const EventType = type === "pointerenter" && typeof PointerEvent === "function" ? PointerEvent : MouseEvent;
        element.dispatchEvent(new EventType(type, { bubbles: true, cancelable: true, view: window }));
      }
      const started = Date.now();
      const poll = () => {
        const candidates = Array.from(document.querySelectorAll('[role="tooltip"],.el-tooltip__popper,.el-popper'));
        const visible = candidates.filter(node => {
          const style = getComputedStyle(node); const rect = node.getBoundingClientRect();
          return node.isConnected && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
        });
        const text = visible.map(node => normalize(node.innerText || node.textContent)).find(Boolean);
        if (text) { resolve({ text, source: "visible-tooltip" }); return; }
        if (Date.now() - started >= ${timeoutMs}) { reject(new Error("tooltip text did not become available before timeout")); return; }
        setTimeout(poll, 50);
      };
      poll();
    })`;
    const result = await callTool("browser_evaluate", { element: args.element, target, function: expression });
    if (!result || result.isError === true)
      return result;
    const parsed = evaluationValue(result);
    if (parsed && typeof parsed.text === "string")
      return textResult(parsed.text);
    return result;
  }

  async function handleToolCall(params) {
    if (!params || typeof params !== "object" || typeof params.name !== "string")
      return textResult("tools/call requires a tool name", true);
    const name = params.name;
    const args = params.arguments && typeof params.arguments === "object" && !Array.isArray(params.arguments)
      ? params.arguments : {};
    try {
      if (name === "browser_snapshot") {
        const snapshotArgs = { ...args };
        if (config.snapshotStrategy === "compact" && snapshotArgs.filename === undefined && snapshotArgs.depth === undefined) {
          snapshotArgs.depth = config.defaultSnapshotDepth;
          if (snapshotArgs.boxes === undefined)
            snapshotArgs.boxes = false;
        }
        if (config.snapshotStrategy === "compact" && snapshotArgs.filename === undefined) {
          delete snapshotArgs.filename;
          return compactSnapshot(snapshotArgs);
        }
        return callTool(name, snapshotArgs);
      }

      if (name === "browser_navigate") {
        const options = waitOptions(args, Math.max(config.settleMs * 4, 5000));
        const explicitWait = Boolean(options.waitForText || options.waitForTextGone || options.waitForSelector || options.waitForUrl);
        const result = await callTool(name, stripWaitOptions(args));
        if (!result || result.isError === true)
          return result;
        if (explicitWait || config.compatibilityMode === "robust") {
          const waited = explicitWait ? await waitForConditions(options) : await waitForDomQuiet();
          if (waited && waited.isError === true)
            return mergeResults(waited, undefined, "Navigation completed, but the requested readiness condition failed.");
        }
        return appendFreshSnapshot(
          result,
          "Navigation completed; fresh page snapshot:",
          explicitWait || config.compatibilityMode === "robust",
        );
      }

      if (name === "browser_click") {
        const clicked = await guardedClick(args);
        return appendFreshSnapshot(
          clicked.result,
          "Click completed; fresh page snapshot:",
          clicked.fallback,
        );
      }

      if (name === "browser_type" && config.compatibilityMode === "robust") {
        requiredString(args, "target");
        requiredString(args, "text");
        const inspected = await inspectField(args);
        const state = inspected && inspected.isError !== true ? evaluationValue(inspected) : undefined;
        if (state && state.disabled)
          return textResult("The target field is disabled; no input was attempted.", true);
        if (state && state.readonly && state.customSelect) {
          const selected = await selectCustomOption({
            target: args.target,
            element: args.element,
            optionText: args.text,
            exact: true,
          });
          if (!selected || selected.isError === true)
            return selected;
          await waitForDomQuiet();
          return appendFreshSnapshot(
            selected,
            "Readonly custom combobox selected by visible option text; fresh page snapshot:",
            true,
          );
        }
        if (state && state.readonly)
          return textResult("The target is readonly and is not a recognized custom combobox; browser_type stopped without waiting for fill timeout.", true);
        const typed = await callTool(name, args);
        return appendFreshSnapshot(typed, "Input completed; fresh page snapshot:");
      }

      if (name === "browser_select_custom_option") {
        const selected = await selectCustomOption(args);
        if (!selected || selected.isError === true)
          return selected;
        await waitForDomQuiet();
        return appendFreshSnapshot(
          selected,
          "Custom option selected; fresh page snapshot:",
          true,
        );
      }

      if (name === "browser_read_tooltip")
        return readTooltip(args);

      if (name === "browser_click_and_wait") {
        requiredString(args, "target");
        const options = waitOptions(args, Math.max(config.settleMs * 6, 5000));
        const clickArgs = stripWaitOptions(args);
        const clicked = await guardedClick(clickArgs);
        if (!clicked.result || clicked.result.isError === true)
          return clicked.result;
        const waited = await waitForConditions(options);
        if (waited && waited.isError === true)
          return mergeResults(waited, undefined,
            "The click was executed, but its postcondition was not verified. Do not repeat the click blindly.");
        return appendFreshSnapshot(
          clicked.result,
          "Click postcondition verified; fresh page snapshot:",
          true,
        );
      }

      const result = await callTool(name, args);
      if (ACTION_TOOLS_WITH_SNAPSHOT.has(name))
        return appendFreshSnapshot(result, "Action completed; fresh page snapshot:");
      return result;
    } catch (error) {
      if (error && error.rpcError)
        throw error;
      return textResult(error instanceof Error ? error.message : String(error), true);
    }
  }

  async function handleRequest(message) {
    if (message.method === "tools/list") {
      const result = await upstreamResult(message.method, message.params || {});
      return { result: enhanceToolList(result, config) };
    }
    if (message.method === "tools/call") {
      const result = await handleToolCall(message.params || {});
      return { result };
    }
    const response = await requestUpstream(message.method, message.params || {});
    return response.error ? { error: response.error } : { result: response.result };
  }

  function dispatchRequest(message) {
    const task = async () => {
      try {
        const response = await handleRequest(message);
        writeMessage({ jsonrpc: "2.0", id: message.id, ...response });
      } catch (error) {
        const rpcError = error && error.rpcError ? error.rpcError : {
          code: -32603,
          message: error instanceof Error ? error.message : String(error),
        };
        writeMessage({ jsonrpc: "2.0", id: message.id, error: rpcError });
      }
    };
    if (message.method === "tools/call") {
      const scheduled = actionQueue.then(task, task);
      actionQueue = scheduled.catch(() => {});
    } else {
      void task();
    }
  }

  const clientLines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  clientLines.on("line", line => {
    if (!line.trim())
      return;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      writeMessage({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
      return;
    }
    if (!message || typeof message !== "object") {
      writeMessage({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "Invalid Request" } });
      return;
    }
    if (typeof message.method === "string" && Object.prototype.hasOwnProperty.call(message, "id")) {
      dispatchRequest(message);
      return;
    }
    sendUpstream(message);
  });
  clientLines.on("close", () => {
    actionQueue.finally(() => {
      if (!childClosed)
        child.stdin.end();
    });
  });
}

function main() {
  const args = process.argv.slice(2);
  const runtimeRoot = path.resolve(__dirname, "..");
  const upstreamCli = path.join(runtimeRoot, "node_modules", "@playwright", "mcp", "cli.js");
  if (!fs.existsSync(upstreamCli) || !fs.statSync(upstreamCli).isFile()) {
    failStartup(`pinned Playwright MCP CLI is missing: ${upstreamCli}`);
    return;
  }
  if (args.includes("--help") || args.includes("-h") || args.includes("--version")) {
    const child = spawn(process.execPath, [upstreamCli, ...args], { stdio: "inherit", windowsHide: true, env: process.env });
    child.on("error", error => failStartup(`cannot start pinned Playwright MCP: ${error.message}`));
    child.on("exit", (code, signal) => {
      process.exitCode = code === 0 ? 0 : (code || (signal ? 2 : 0));
    });
    return;
  }
  let config;
  try {
    config = loadInteractionConfig(args);
  } catch (error) {
    failStartup(error instanceof Error ? error.message : String(error));
    return;
  }
  runProxy(upstreamCli, args, config);
}

main();
