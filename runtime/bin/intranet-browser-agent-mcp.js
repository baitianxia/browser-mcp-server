#!/usr/bin/env node
"use strict";

// Offline stdio compatibility layer for the pinned @playwright/mcp runtime.
// It deliberately uses Node.js built-ins only so the target host never needs a
// package-manager repair step.

const fs = require("fs");
const os = require("os");
const path = require("path");
const readline = require("readline");
const { StringDecoder } = require("string_decoder");
const { fileURLToPath } = require("url");
const { spawn } = require("child_process");

const INTERNAL_ID_PREFIX = `intranet-${process.pid}-${Date.now()}-`;
const MAX_COMPACT_SNAPSHOT_CHARS = 16000;
const ARTIFACT_TOOLS = new Set([
  "browser_console_messages",
  "browser_evaluate",
  "browser_network_request",
  "browser_network_requests",
  "browser_snapshot",
  "browser_take_screenshot",
  "browser_pdf_save",
  "browser_start_video",
  "browser_stop_video",
  "browser_storage_state",
]);
const ARTIFACT_FILENAME_TOOLS = new Set(ARTIFACT_TOOLS);
const DOWNLOAD_TRIGGER_TOOLS = new Set([
  "browser_click",
  "browser_click_and_wait",
  "browser_click_text",
  "browser_click_pointer",
  "browser_navigate",
  "browser_navigate_back",
  "browser_navigate_forward",
  "browser_reload",
  "browser_press_key",
  "browser_run_code_unsafe",
  "browser_type",
  "browser_mouse_click_xy",
]);
const DOWNLOAD_WAIT_TOOL = "browser_wait_for_download";
const DOWNLOAD_GRACE_MS = 250;
const DOWNLOAD_SETTLE_MS = 120;
const DOWNLOAD_MAX_WAIT_MS = 10000;
const ACTION_TOOLS_WITH_SNAPSHOT = new Set([
  "browser_click",
  "browser_drag",
  "browser_drop",
  "browser_fill_form",
  "browser_handle_dialog",
  "browser_hover",
  "browser_mouse_click_xy",
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
const ACTIONABILITY_FAILURE = /(?:timed?\s*out|timeout|not\s+(?:visible|stable|enabled|actionable)|outside\s+(?:of\s+)?the\s+viewport|intercepts?\s+pointer|(?:does\s+not|is\s+not)\s+receiv(?:e|ing)\s+pointer|obscured|covered\s+by\s+another|detached|not\s+attached|element\s+is\s+not\s+visible|waiting\s+for\s+element\s+to\s+be\s+visible)/i;
const AMBIGUOUS_TARGET_FAILURE = /(?:strict\s+mode\s+violation|resolved\s+to\s+\d+\s+elements?|matched\s+\d+\s+elements?|multiple\s+elements?)/i;
const INFRASTRUCTURE_FAILURE = /(?:browser\b[^\n]{0,40}\b(?:disconnected|closed)|target\s+(?:page|context|browser)\s+[^\n]{0,40}\bclosed|connection\s+(?:closed|reset|lost)|protocol\s+error|transport\s+error)/i;

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
  if (typeof process.env.PLAYWRIGHT_MCP_CONFIG === "string" && process.env.PLAYWRIGHT_MCP_CONFIG.trim())
    return path.resolve(process.env.PLAYWRIGHT_MCP_CONFIG);
  return undefined;
}

function outputDirectoryFromArgs(args) {
  for (let index = 0; index < args.length; index += 1) {
    if ((args[index] === "--output-dir" || args[index] === "--outputDir") && index + 1 < args.length)
      return args[index + 1];
    if (args[index].startsWith("--output-dir=") || args[index].startsWith("--outputDir="))
      return args[index].slice(args[index].indexOf("=") + 1);
  }
  return undefined;
}

function defaultOutputDirectory(skillMode = false) {
  const cwd = path.resolve(process.cwd());
  let useTemporaryDirectory = cwd === path.parse(cwd).root;
  if (!useTemporaryDirectory && process.platform === "win32") {
    const systemRoot = path.resolve(process.env.SystemRoot || "C:\\Windows");
    const relative = path.relative(systemRoot.toLowerCase(), cwd.toLowerCase());
    useTemporaryDirectory = relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
  }
  if (!useTemporaryDirectory) {
    try {
      fs.accessSync(cwd, fs.constants.W_OK);
    } catch {
      useTemporaryDirectory = true;
    }
  }
  return path.join(useTemporaryDirectory ? os.tmpdir() : cwd, skillMode ? ".playwright-cli" : ".playwright-mcp");
}

function loadPlaywrightConfigValue(playwrightConfig) {
  if (!playwrightConfig || !fs.existsSync(playwrightConfig))
    return {};
  const raw = fs.readFileSync(playwrightConfig, "utf8");
  const data = raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw;
  try {
    return JSON.parse(data);
  } catch (jsonError) {
    // @playwright/mcp also accepts its documented flat INI form. The wrapper
    // only needs the output root (and the optional skill-mode hint) to make
    // artifact paths safe; the upstream process remains the source of truth
    // for all other configuration keys and validation.
    if (/^\s*\{/.test(data))
      throw jsonError;
    const value = {};
    for (const rawLine of data.split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#") || line.startsWith(";") || line.startsWith("["))
        continue;
      const separator = line.search(/[=:]/);
      if (separator < 1)
        continue;
      const key = line.slice(0, separator).trim();
      let parsed = line.slice(separator + 1).trim();
      if ((parsed.startsWith('"') && parsed.endsWith('"')) ||
          (parsed.startsWith("'") && parsed.endsWith("'")))
        parsed = parsed.slice(1, -1);
      if (key === "outputDir")
        value.outputDir = parsed;
      else if (key === "skillMode")
        value.skillMode = parsed === "true" || parsed === "1";
    }
    return value;
  }
}

function loadPlaywrightSettings(args) {
  const playwrightConfig = configPathFromArgs(args);
  let value;
  try {
    value = loadPlaywrightConfigValue(playwrightConfig);
  } catch (error) {
    throw new Error(`cannot load Playwright output directory: ${error.message}`);
  }
  const cliConfigured = outputDirectoryFromArgs(args);
  const environmentConfigured = process.env.PLAYWRIGHT_MCP_OUTPUT_DIR;
  const configured = [
    cliConfigured,
    environmentConfigured,
    value && typeof value.outputDir === "string" ? value.outputDir.trim() : "",
  ].find(item => typeof item === "string" && item.trim());
  const outputDirectory = path.resolve(configured || defaultOutputDirectory(Boolean(value && value.skillMode)));
  try {
    fs.mkdirSync(outputDirectory, { recursive: true });
    return { configPath: playwrightConfig, outputDirectory };
  } catch (error) {
    throw new Error(`cannot load Playwright output directory: ${error.message}`);
  }
}

function normalizeUpstreamArgs(args, settings) {
  const normalized = [...args];
  for (let index = 0; index < normalized.length; index += 1) {
    if (normalized[index] === "--outputDir") {
      normalized[index] = "--output-dir";
    } else if (normalized[index].startsWith("--outputDir=")) {
      normalized[index] = `--output-dir=${normalized[index].slice("--outputDir=".length)}`;
    }
    if (!settings || !settings.configPath)
      continue;
    if (normalized[index] === "--config" && index + 1 < normalized.length) {
      normalized[index + 1] = settings.configPath;
      index += 1;
    } else if (normalized[index].startsWith("--config=")) {
      normalized[index] = `--config=${settings.configPath}`;
    }
  }
  return normalized;
}

function isWithinDirectory(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

function lstatExists(candidate) {
  try {
    fs.lstatSync(candidate);
    return true;
  } catch (error) {
    return !error || !["ENOENT", "ENOTDIR"].includes(error.code);
  }
}

function resolvesInsideDirectory(root, candidate, realRoot = fs.realpathSync(root)) {
  let existing = candidate;
  while (!lstatExists(existing) && isWithinDirectory(root, existing))
    existing = path.dirname(existing);
  if (!isWithinDirectory(root, existing))
    return false;
  try {
    return isWithinDirectory(realRoot, fs.realpathSync(existing));
  } catch {
    return false;
  }
}

function localWorkspacePath(value, fallback) {
  if (typeof value !== "string" || !value.trim())
    return fallback;
  if (path.isAbsolute(value))
    return path.resolve(value);
  if (/^file:\/\//i.test(value)) {
    try {
      const parsed = fileURLToPath(value);
      if (path.isAbsolute(parsed))
        return path.resolve(parsed);
    } catch {
      // Fall through to the safe negotiated workspace.
    }
  }
  return fallback;
}

function normalizeArtifactFilename(toolName, args, outputDirectory) {
  if (!ARTIFACT_FILENAME_TOOLS.has(toolName) || args.filename === undefined || !outputDirectory)
    return args;
  if (typeof args.filename !== "string" || !args.filename.trim())
    throw new Error("filename must be a non-empty local path inside the configured output directory");
  if ((!path.isAbsolute(args.filename) && /^[a-z][a-z0-9+.-]*:/i.test(args.filename)) || args.filename.includes("\0"))
    throw new Error("filename must be a local path, not a URL or NUL-containing value");
  const candidate = path.resolve(outputDirectory, args.filename);
  if (!isWithinDirectory(outputDirectory, candidate))
    throw new Error("filename must remain inside the configured output directory");
  const realRoot = fs.realpathSync(outputDirectory);
  let existingParent = path.dirname(candidate);
  while (!lstatExists(existingParent) && isWithinDirectory(outputDirectory, existingParent))
    existingParent = path.dirname(existingParent);
  if (!isWithinDirectory(outputDirectory, existingParent))
    throw new Error("filename parent resolves outside the configured output directory");
  const realExistingParent = fs.realpathSync(existingParent);
  if (!isWithinDirectory(realRoot, realExistingParent))
    throw new Error("filename parent resolves through a link outside the configured output directory");
  fs.mkdirSync(path.dirname(candidate), { recursive: true });
  const realParent = fs.realpathSync(path.dirname(candidate));
  if (!isWithinDirectory(realRoot, realParent))
    throw new Error("filename resolves through a link outside the configured output directory");
  if (lstatExists(candidate)) {
    let realCandidate;
    try { realCandidate = fs.realpathSync(candidate); } catch {
      throw new Error("filename cannot be resolved inside the configured output directory (possibly a dangling link)");
    }
    if (!isWithinDirectory(realRoot, realCandidate))
      throw new Error("filename resolves through a link outside the configured output directory");
  }
  // Playwright MCP treats an explicit filename as a workspace file. Passing
  // the validated absolute path makes its own output/workspace containment
  // check accept the configured output directory without changing child cwd.
  return { ...args, filename: candidate };
}

function artifactCandidates(text) {
  if (typeof text !== "string" || !text)
    return [];
  const normalizedText = text.replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  const values = [];
  const add = value => {
    if (typeof value === "string" && value.trim())
      values.push(value.trim().replace(/^<|>$/g, ""));
  };
  let match;
  const markdown = /!?\[[^\]]*\]\(([^)\n]+)\)/g;
  while ((match = markdown.exec(normalizedText)) !== null)
    add(match[1].split("#", 1)[0]);
  const quoted = /(?:download(?:ed|ing)?|saved?|written|output|path)[^\n]{0,80}?\s["'`]<([^>]+)>["'`]|(?:download(?:ed|ing)?|saved?|written|output|path)[^\n]{0,80}?\s["'`]([^"'`\n]+)["'`]/gi;
  while ((match = quoted.exec(normalizedText)) !== null)
    add(match[1] || match[2]);
  const pathLike = /(?:^|[\s"'`])((?:[A-Za-z]:[\\/]|\.{0,2}[\\/]|\/)[^\s"'`),?\.(?:png|jpe?g|svg|ico|webp|gif|tiff?|pdf|md|json|jsonl|ndjson|csv|tsv|txt|log|zip|rar|7z|tar|gz|bz2|xz|tgz|xlsx?|docx?|pptx?|bin|exe|msi|dmg|apk|deb|rpm|jar|wasm|mp4|m4v|webm|mov|avi|mkv|mp3|wav|ogg|html?|css|js|xml|ya?ml|sql|parquet)(?:$|[\s"'`)])/gi;
  while ((match = pathLike.exec(normalizedText)) !== null)
    add(match[1]);
  const relative = /(?:^|[\s"'`])([A-Za-z0-9_.-]+(?:[\\/][A-Za-z0-9_.-]+)*\.(?:png|jpe?g|svg|ico|webp|gif|tiff?|pdf|md|json|jsonl|ndjson|csv|tsv|txt|log|zip|rar|7z|tar|gz|bz2|xz|tgz|xlsx?|docx?|pptx?|bin|exe|msi|dmg|apk|deb|rpm|jar|wasm|mp4|m4v|webm|mov|avi|mkv|mp3|wav|ogg|html?|css|js|xml|ya?ml|sql|parquet))(?:$|[\s"'`)])/gi;
  while ((match = relative.exec(normalizedText)) !== null)
    add(match[1]);
  return values;
}

function resolveArtifactPath(candidate, outputDirectory, workspaceDirectory = process.cwd()) {
  if (typeof candidate !== "string" || !candidate.trim())
    return undefined;
  let value = candidate.trim();
  if (value.includes("\0"))
    return undefined;
  if (/^[a-z][a-z0-9+.-]*:/i.test(value) && !/^file:\/\//i.test(value) && !/^[a-z]:[\\/]/i.test(value))
    return undefined;
  if (/^file:\/\//i.test(value)) {
    try {
      value = fileURLToPath(value);
    } catch {
      return undefined;
    }
  }
  const root = outputDirectory ? path.resolve(outputDirectory) : path.resolve(process.cwd());
  let realRoot;
  if (outputDirectory) {
    try {
      realRoot = fs.realpathSync(root);
    } catch {
      return undefined;
    }
  }
  const workspaceRoot = path.resolve(workspaceDirectory || process.cwd());
  const lexicalCandidates = path.isAbsolute(value)
    ? [path.normalize(value)]
    : [
        path.resolve(root, value),
        ...(workspaceRoot !== root
          ? [path.resolve(workspaceRoot, value)]
          : []),
      ];
  const valid = [];
  for (const resolved of lexicalCandidates) {
    if (outputDirectory && !isWithinDirectory(root, resolved))
      continue;
    if (outputDirectory && !resolvesInsideDirectory(root, resolved, realRoot))
      continue;
    valid.push(resolved);
  }
  if (!valid.length)
    return undefined;
  return valid.find(candidatePath => fs.existsSync(candidatePath)) || valid[0];
}

function artifactKind(toolName, filePath) {
  if (DOWNLOAD_TRIGGER_TOOLS.has(toolName) || toolName === DOWNLOAD_WAIT_TOOL)
    return "download";
  if (toolName.includes("screenshot") || /\.(?:png|jpe?g|webp|gif)$/i.test(filePath))
    return "screenshot";
  if (toolName.includes("snapshot") || /\.(?:md|ya?ml)$/i.test(filePath))
    return "snapshot";
  if (toolName.includes("pdf") || /\.pdf$/i.test(filePath))
    return "pdf";
  if (toolName.includes("video") || /\.(?:mp4|webm)$/i.test(filePath))
    return "video";
  if (toolName.includes("storage"))
    return "storage";
  if (toolName.includes("network") || toolName.includes("console") || toolName.includes("evaluate"))
    return "text";
  return "download";
}

function outputFiles(root, depth = 0, result = new Map()) {
  if (!root || depth > 8 || !fs.existsSync(root))
    return result;
  let entries;
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch {
    return result;
  }
  for (const entry of entries) {
    const filePath = path.join(root, entry.name);
    if (entry.isSymbolicLink())
      continue;
    if (entry.isDirectory()) {
      outputFiles(filePath, depth + 1, result);
      continue;
    }
    if (!entry.isFile())
      continue;
    try {
      const stat = fs.statSync(filePath);
      result.set(filePath, { size: stat.size, mtimeMs: stat.mtimeMs, ctimeMs: stat.ctimeMs, ino: stat.ino });
    } catch {
      // A file can disappear while the upstream output evictor is running.
    }
  }
  return result;
}

function changedOutputArtifacts(before, root, toolName) {
  if (!root)
    return [];
  const after = outputFiles(root);
  const changed = [];
  for (const [filePath, stat] of after.entries()) {
    const previous = before && before.get(filePath);
    if (!previous || previous.size !== stat.size || previous.mtimeMs !== stat.mtimeMs ||
        previous.ctimeMs !== stat.ctimeMs || previous.ino !== stat.ino)
      changed.push({ path: filePath, status: "ready", kind: artifactKind(toolName, filePath) });
  }
  return changed;
}

function timerShimCode(originalCode) {
  if (typeof originalCode !== "string" || !originalCode.trim())
    return originalCode;
  if (originalCode.includes("intranet-browser-agent-timer-shim"))
    return originalCode;
  let source = originalCode.trim();
  // The upstream VM evaluates a function expression. Strip only wrappers
  // outside that expression so common trailing semicolons/comments do not
  // become part of the parenthesized call below.
  for (;;) {
    const withoutSemicolon = source.replace(/;\s*$/, "");
    const withoutComment = withoutSemicolon.replace(/(?:\s*\/\/[^\r\n]*|\s*\/\*[\s\S]*?\*\/)+\s*$/, "");
    if (withoutComment === source)
      break;
    source = withoutComment;
  }
  // browser_run_code_unsafe receives a function expression. Wrap it in another
  // function so the shim lives in the upstream VM, without exposing Node APIs.
  return `(async (page) => {
  /* intranet-browser-agent-timer-shim */
  const __intranetSleep = (ms) => {
    const delay = Math.max(0, Number(ms) || 0);
    if (page && typeof page.waitForTimeout === "function") return page.waitForTimeout(delay);
    if (page && typeof page.evaluate === "function") return page.evaluate(({ delay: value }) => new Promise(resolve => setTimeout(resolve, value)), { delay });
    return Promise.resolve();
  };
  const __intranetSchedule = (callback, delay, values) => {
    if (typeof callback !== "function") throw new TypeError("callback must be a function");
    const handle = { active: true };
    __intranetSleep(delay).then(() => {
      if (!handle.active) return;
      handle.active = false;
      return callback(...values);
    });
    return handle;
  };
  try {
    if (typeof globalThis.setTimeout !== "function")
      globalThis.setTimeout = (callback, delay, ...values) => __intranetSchedule(callback, delay, values);
    if (typeof globalThis.clearTimeout !== "function")
      globalThis.clearTimeout = handle => { if (handle) handle.active = false; };
    if (typeof globalThis.setInterval !== "function") {
      globalThis.setInterval = (callback, delay, ...values) => {
        const handle = { active: true };
        const tick = async () => {
          while (handle.active) {
            await __intranetSleep(delay);
            if (handle.active) await callback(...values);
          }
        };
        void tick().catch(() => { handle.active = false; });
        return handle;
      };
    }
    if (typeof globalThis.clearInterval !== "function")
      globalThis.clearInterval = handle => { if (handle) handle.active = false; };
  } catch {
    // A hardened VM may reject global assignment; page.waitForTimeout remains available.
  }
  return await (${source})(page);
})`;
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
    const raw = fs.readFileSync(interactionPath, "utf8");
    value = JSON.parse(raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw);
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
  const primaryStructured = primary && primary.structuredContent && typeof primary.structuredContent === "object"
    ? primary.structuredContent
    : undefined;
  const snapshotStructured = snapshot && snapshot.structuredContent && typeof snapshot.structuredContent === "object"
    ? snapshot.structuredContent
    : undefined;
  const merged = {
    ...(primary && typeof primary === "object" ? primary : {}),
    content: [
      ...first,
      ...(note ? [{ type: "text", text: note }] : []),
      ...second,
    ],
  };
  if (primaryStructured || snapshotStructured) {
    merged.structuredContent = {
      ...(primaryStructured || {}),
      ...(snapshotStructured || {}),
    };
    const artifacts = [
      ...(Array.isArray(primaryStructured && primaryStructured.artifacts) ? primaryStructured.artifacts : []),
      ...(Array.isArray(snapshotStructured && snapshotStructured.artifacts) ? snapshotStructured.artifacts : []),
    ];
    if (artifacts.length)
      merged.structuredContent.artifacts = artifacts;
  }
  return merged;
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
    description: "Snapshot ref, unique CSS selector, or Playwright locator expression such as getByText(...) / getByRole(...)",
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
      description: "Click a snapshot/locator target or live visible text with the configured compatibility policy, wait for an explicit page condition (or DOM stability), then return a fresh snapshot using the configured strategy",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        anyOf: [{ required: ["target"] }, { required: ["text"] }],
        properties: {
          target,
          text: { type: "string", minLength: 1, description: "Live visible text to locate when a snapshot ref is unavailable" },
          role: { type: "string", minLength: 1, description: "Optional ARIA role used with text" },
          exact: { type: "boolean", description: "Require exact text/name matching when text is provided; defaults to true" },
          element,
          force: { type: "boolean", description: "Allow the guarded same-target DOM fallback after native click failure" },
          pointer: { type: "boolean", description: "Use verified real pointer input; requires the vision capability" },
          doubleClick: { type: "boolean" },
          button: { enum: ["left", "right", "middle"] },
          modifiers: { type: "array", items: { enum: ["Alt", "Control", "ControlOrMeta", "Meta", "Shift"] } },
          ...waits,
        },
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    {
      name: "browser_click_pointer",
      title: "Click with real pointer events",
      description: "Resolve one visible target, verify that its center receives pointer events, then click through the vision mouse tool. Use for React/Vue controls that do not respond to programmatic DOM click.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["target"],
        properties: {
          target,
          element,
          button: { enum: ["left", "right", "middle"] },
          doubleClick: { type: "boolean" },
        },
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    {
      name: "browser_click_text",
      title: "Click visible text",
      description: "Click a unique live DOM element by visible text or role without requiring a snapshot ref. Exact matching is enabled by default.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["text"],
        properties: {
          text: { type: "string", minLength: 1 },
          role: { type: "string", minLength: 1, description: "Optional ARIA role, for example menuitem or button" },
          exact: { type: "boolean", description: "Require exact text/name match; defaults to true" },
          element,
          force: { type: "boolean", description: "Allow the guarded same-target DOM fallback after native click failure" },
          pointer: { type: "boolean", description: "Use real pointer input instead of native locator click" },
          doubleClick: { type: "boolean" },
        },
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    {
      name: "browser_clipboard",
      title: "Read or write page clipboard",
      description: "Use the page Clipboard API when available. Reports secure-context and permission failures explicitly; it never broadens browser permissions automatically.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        required: ["operation"],
        properties: {
          operation: { enum: ["read", "write"] },
          text: { type: "string", description: "Text to write when operation=write" },
          target,
          element,
          grantPermissions: { type: "boolean", description: "Explicitly grant clipboard-read/write for the current page origin before attempting the operation" },
        },
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    {
      name: "browser_wait_for_download",
      title: "Wait for download",
      description: "Wait for a Playwright download already triggered by the preceding action and return its absolute output path.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: { timeoutMs: timeout },
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
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
    if (tool.name === "browser_click") {
      copy.inputSchema = JSON.parse(JSON.stringify(tool.inputSchema || { type: "object", properties: {} }));
      copy.inputSchema.properties = copy.inputSchema.properties || {};
      copy.inputSchema.properties.force = {
        type: "boolean",
        description: "Explicitly allow the guarded same-target DOM fallback after native click failure. This is programmatic input; use pointer=true for real mouse events.",
      };
      copy.inputSchema.properties.pointer = {
        type: "boolean",
        description: "Use a verified center coordinate and browser_mouse_click_xy for real pointer events. Requires the vision capability.",
      };
      copy.inputSchema.properties.text = {
        type: "string",
        minLength: 1,
        description: "Live visible text to locate when a snapshot ref is unavailable; use role for an ARIA role.",
      };
      copy.inputSchema.properties.role = {
        type: "string",
        minLength: 1,
        description: "Optional ARIA role used with text, for example menuitem or button.",
      };
      copy.inputSchema.properties.exact = {
        type: "boolean",
        description: "Require exact text/name matching when text is provided; defaults to true.",
      };
      const required = Array.isArray(copy.inputSchema.required) ? copy.inputSchema.required : [];
      const requiredWithoutTarget = required.filter(key => key !== "target");
      copy.inputSchema.required = requiredWithoutTarget;
      copy.inputSchema.anyOf = [
        { required: [...requiredWithoutTarget, "target"] },
        { required: [...requiredWithoutTarget, "text"] },
      ];
      copy.description = `${tool.description || "Click an element"}. Accepts a live text/ARIA locator or Playwright locator expression (for example getByText(...)); native actionability is tried first, with explicit force or pointer fallbacks.`;
    }
    if (tool.name === "browser_run_code_unsafe")
      copy.description = `${tool.description || "Run Playwright code"}. The compatibility layer injects only page-backed timer shims for missing setTimeout/setInterval; no Node globals are exposed.`;
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

function runProxy(upstreamCli, upstreamArgs, config, settings = {}) {
  const outputDirectory = settings.outputDirectory;
  const effectiveUpstreamArgs = normalizeUpstreamArgs(upstreamArgs, settings);
  const child = spawn(process.execPath, [upstreamCli, ...effectiveUpstreamArgs], {
    stdio: ["pipe", "pipe", "pipe"],
    windowsHide: true,
    env: process.env,
  });
  let internalSequence = 0;
  let childClosed = false;
  const pending = new Map();
  let actionQueue = Promise.resolve();
  let downloadSequence = 0;
  let lastDownloadEventAt = 0;
  const downloadRecords = [];
  const pendingRootRequests = new Set();
  let clientWorkspace = process.cwd();
  let activeRequestMeta;
  let activeRequestWorkspace = clientWorkspace;

  function downloadDestinationCandidates(text) {
    if (typeof text !== "string" || !text)
      return [];
    const normalized = text.replace(/\\"/g, '"').replace(/\\\\/g, "\\");
    const values = [];
    const add = value => {
      if (typeof value === "string" && value.trim())
        values.push(value.trim().replace(/^<|>$/g, ""));
    };
    const destination = /(?:\bto|\b(?:saved?|written|output|path))[^\n]{0,80}?\s["'`]<([^>]+)>["'`]|(?:\bto|\b(?:saved?|written|output|path))[^\n]{0,80}?\s["'`]([^"'`\n]+)["'`]/gi;
    let match;
    while ((match = destination.exec(normalized)) !== null)
      add(match[1] || match[2]);
    const markdown = /!?(?:\[[^\]]*\])\(([^)\n]+)\)/g;
    while ((match = markdown.exec(normalized)) !== null)
      add(match[1].split("#", 1)[0]);
    return values;
  }

  function recordDownloadText(text) {
    if (typeof text !== "string" || !text)
      return;
    // Only consume event-shaped lines. A page URL or ordinary console text
    // containing "downloaded" must not create a phantom download record.
    const eventLines = text.split(/\r?\n/)
      .map(line => line.trim())
      .filter(line => /^(?:[-*]\s*)?(?:downloading\b|downloaded\b|download\s+(?:started|finished|finish|complete|completed)\b)/i.test(line));
    if (!eventLines.length)
      return;
    const eventText = eventLines.join("\n");
    const lower = eventText.toLowerCase();
    const started = /\bdownloading\b|\bdownload\s+started\b/.test(lower);
    const finished = /\bdownloaded\b|\bdownload\s+(?:finished|complete|completed)\b/.test(lower);
    if (!started && !finished)
      return;
    const destinationCandidates = downloadDestinationCandidates(eventText);
    const hasExplicitDestination = destinationCandidates.length > 0;
    const pendingRecord = [...downloadRecords].reverse().find(item => item.status === "started");
    const candidateWorkspace = pendingRecord && pendingRecord.workspace || activeRequestWorkspace;
    const candidates = [...destinationCandidates, ...artifactCandidates(eventText)]
      .filter((candidate, index, all) => all.indexOf(candidate) === index)
      .map(candidate => resolveArtifactPath(candidate, outputDirectory, candidateWorkspace))
      .filter(Boolean);
    // A start notification usually contains only the suggested filename
    // (for example, "report.pdf"), not the eventual output path. Capture the
    // inventory before resolving candidates so a stale file with that basename
    // cannot become a false pending download. A finish message with an explicit
    // destination is trusted; otherwise a pending record or the post-call
    // output inventory supplies the eventual path.
    const beforeFiles = started ? outputFiles(outputDirectory) : undefined;
    const filePath = candidates.find(candidate => fs.existsSync(candidate) &&
      (!started || hasExplicitDestination || (finished && pendingRecord)));
    let record = filePath
      ? downloadRecords.find(item => item.path === filePath && ["started", "finished"].includes(item.status))
        || pendingRecord
        || (started && finished && [...downloadRecords].reverse().find(item => item.status === "finished"))
      : pendingRecord
        || (started && finished && [...downloadRecords].reverse().find(item => item.status === "finished"));
    if (!record) {
      record = {
        path: filePath,
        status: finished ? "finished" : "started",
        sequence: ++downloadSequence,
        text: eventText,
        workspace: candidateWorkspace,
        ...(started ? { beforeFiles } : {}),
      };
      downloadRecords.push(record);
    } else {
      const previousStatus = record.status;
      record.status = finished ? "finished" : record.status;
      if (filePath && (!record.path || previousStatus === "started"))
        record.path = filePath;
      record.text = eventText;
      record.sequence = ++downloadSequence;
    }
    if (finished && !record.path && record.beforeFiles) {
      const inferred = changedOutputArtifacts(record.beforeFiles, outputDirectory, DOWNLOAD_WAIT_TOOL);
      if (inferred.length === 1)
        record.path = inferred[0].path;
    }
    lastDownloadEventAt = Date.now();
    while (downloadRecords.length > 50)
      downloadRecords.shift();
  }

  function recordStructuredDownloadEvent(value) {
    if (!value || typeof value !== "object" || Array.isArray(value))
      return;
    const event = value.download && typeof value.download === "object" && !Array.isArray(value.download)
      ? value.download
      : value;
    const type = String(event.type || event.event || event.status || event.state || "").toLowerCase();
    const pathValue = event.path || event.outputFile || event.filePath || event.destination;
    const suggested = event.suggestedFilename || event.filename || event.name;
    if (typeof pathValue !== "string" && typeof suggested !== "string")
      return;
    const started = /start|begin|pending/.test(type) && !/finish|complete/.test(type);
    const label = typeof suggested === "string" && suggested.trim()
      ? suggested.trim()
      : (typeof pathValue === "string" && pathValue.trim() ? pathValue.trim() : "download");
    const line = started
      ? `Downloading file ${label} ...`
      : `Downloaded file ${label}${typeof pathValue === "string" && pathValue.trim() ? ` to "${pathValue.trim()}"` : ""}`;
    recordDownloadText(line);
  }

  function recentDownloadArtifacts(sequence) {
    return downloadRecords
      .filter(item => item.sequence > sequence && item.path)
      .map(item => ({ path: item.path, status: item.status, kind: "download" }));
  }

  function attachDownloadArtifacts(sequence, artifacts) {
    const available = artifacts.filter(item => item && typeof item.path === "string");
    if (!available.length)
      return;
    const used = new Set();
    for (const record of downloadRecords) {
      if (record.sequence <= sequence || !["started", "finished", "timeout"].includes(record.status))
        continue;
      const artifact = record.path
        ? available.find(item => item.path === record.path)
        : available.find(item => !used.has(item.path));
      const filePath = artifact && artifact.path;
      if (!filePath)
        continue;
      record.path = filePath;
      // A newly observed output file is the strongest completion signal when
      // the upstream finish notification was delayed or pathless.
      record.status = "finished";
      artifact.status = "finished";
      artifact.kind = "download";
      used.add(filePath);
    }
  }

  async function waitForDownloadWindow(sequence, maxWaitMs = DOWNLOAD_MAX_WAIT_MS, beforeFiles) {
    const startedAt = Date.now();
    const deadline = startedAt + Math.max(0, maxWaitMs);
    const reconcileOutputFile = () => {
      if (!beforeFiles)
        return false;
      const records = downloadRecords.filter(item => item.sequence > sequence &&
        ["started", "finished"].includes(item.status));
      if (!records.length)
        return false;
      const changed = changedOutputArtifacts(beforeFiles, outputDirectory, DOWNLOAD_WAIT_TOOL);
      if (!changed.length)
        return false;
      const knownPath = records.some(item => item.path && changed.some(file => file.path === item.path));
      if (changed.length !== 1 && !knownPath)
        return false;
      attachDownloadArtifacts(sequence, changed);
      return true;
    };
    if (maxWaitMs <= 0) {
      for (const item of downloadRecords) {
        if (item.sequence > sequence && item.status === "started")
          item.status = "timeout";
      }
      return;
    }
    const graceDeadline = Math.min(deadline, startedAt + DOWNLOAD_GRACE_MS);
    let sawEvent = false;
    while (Date.now() < graceDeadline) {
      if (reconcileOutputFile())
        return;
      if (downloadSequence > sequence) {
        sawEvent = true;
        const pendingDownload = downloadRecords.some(item => item.sequence > sequence && item.status === "started");
        if (!pendingDownload && Date.now() - lastDownloadEventAt >= DOWNLOAD_SETTLE_MS)
          return;
      }
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    if (!sawEvent)
      return;
    while (Date.now() < deadline) {
      if (reconcileOutputFile())
        return;
      const pendingDownload = downloadRecords.some(item => item.sequence > sequence && item.status === "started");
      if (!pendingDownload && Date.now() - lastDownloadEventAt >= DOWNLOAD_SETTLE_MS)
        return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    for (const item of downloadRecords) {
      if (item.sequence > sequence && item.status === "started")
        item.status = "timeout";
    }
  }

  function decorateArtifacts(result, toolName, args, sequence, extraArtifacts = []) {
    if (!result || typeof result !== "object")
      return result;
    const resultText = textFromResult(result);
    const candidates = (DOWNLOAD_TRIGGER_TOOLS.has(toolName) || toolName === DOWNLOAD_WAIT_TOOL)
      ? downloadDestinationCandidates(resultText)
      : artifactCandidates(resultText);
    if (ARTIFACT_FILENAME_TOOLS.has(toolName) && args && typeof args.filename === "string" && outputDirectory)
      candidates.push(args.filename);
    const existingArtifacts = result.structuredContent && typeof result.structuredContent === "object" &&
      Array.isArray(result.structuredContent.artifacts)
      ? result.structuredContent.artifacts
      : [];
    const normalizedExistingArtifacts = existingArtifacts
      .map(item => {
        const resolved = item && resolveArtifactPath(item.path, outputDirectory, activeRequestWorkspace);
        return resolved ? { ...item, path: resolved } : undefined;
      })
      .filter(Boolean);
    const records = [
      ...normalizedExistingArtifacts,
      ...extraArtifacts,
      ...recentDownloadArtifacts(sequence),
      ...candidates
        .map(candidate => resolveArtifactPath(candidate, outputDirectory, activeRequestWorkspace))
        .filter(filePath => Boolean(filePath) &&
          (!DOWNLOAD_TRIGGER_TOOLS.has(toolName) && toolName !== DOWNLOAD_WAIT_TOOL || fs.existsSync(filePath)))
        .map(filePath => ({ path: filePath, status: fs.existsSync(filePath) ? "ready" : "pending", kind: artifactKind(toolName, filePath) })),
    ];
    const unique = [];
    const seen = new Map();
    const statusRank = { pending: 0, ready: 1, timeout: 2, finished: 3 };
    for (const item of records) {
      if (!item || typeof item.path !== "string")
        continue;
      const relativePath = outputDirectory ? path.relative(outputDirectory, item.path) : undefined;
      const normalized = {
        path: item.path,
        ...(relativePath && !relativePath.startsWith("..") ? { relativePath } : {}),
        status: item.status || (fs.existsSync(item.path) ? "ready" : "pending"),
        kind: item.kind || artifactKind(toolName, item.path),
      };
      const existingIndex = seen.get(item.path);
      if (existingIndex === undefined) {
        seen.set(item.path, unique.length);
        unique.push(normalized);
      } else if ((statusRank[normalized.status] || 0) > (statusRank[unique[existingIndex].status] || 0)) {
        unique[existingIndex] = { ...unique[existingIndex], ...normalized };
      }
    }
    if (!unique.length)
      return result;
    const structuredContent = {
      ...(result.structuredContent && typeof result.structuredContent === "object" ? result.structuredContent : {}),
      artifacts: unique,
    };
    const lines = unique.map(item => `- ${item.path} (${item.status})`).join("\n");
    return {
      ...result,
      structuredContent,
      content: [
        ...(Array.isArray(result.content) ? result.content : []),
        { type: "text", text: `Artifact paths (absolute):\n${lines}` },
      ],
    };
  }

  for (const stream of [process.stdout, process.stderr]) {
    try { stream.setDefaultEncoding("utf8"); } catch {
      // Some embedded clients expose a read-only standard stream.
    }
  }
  const stderrDecoder = new StringDecoder("utf8");
  child.stderr.on("data", data => process.stderr.write(stderrDecoder.write(data)));
  child.stderr.on("end", () => {
    const tail = stderrDecoder.end();
    if (tail)
      process.stderr.write(tail);
  });
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
    if (message && message.params && typeof message.params === "object") {
      for (const value of [message.params.data, message.params.message, message.params.text])
        if (typeof value === "string") recordDownloadText(value);
      if (/download/i.test(String(message.method || "")) || message.params.download)
        recordStructuredDownloadEvent(message.params);
      if (message.params.data && typeof message.params.data === "object" &&
          (/download/i.test(String(message.method || "")) ||
            /download/i.test(String(message.params.data.type || message.params.data.event || "")) ||
            message.params.data.download))
        recordStructuredDownloadEvent(message.params.data);
    }
    if (message && message.method === "roots/list" && Object.prototype.hasOwnProperty.call(message, "id"))
      pendingRootRequests.add(message.id);
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
    const forwardedArguments = { ...(args || {}) };
    if (activeRequestMeta && forwardedArguments._meta === undefined)
      forwardedArguments._meta = activeRequestMeta;
    return upstreamResult("tools/call", { name, arguments: forwardedArguments });
  }

  function clickArguments(args) {
    const result = { ...(args || {}) };
    delete result.force;
    delete result.pointer;
    delete result.text;
    delete result.role;
    delete result.exact;
    return result;
  }

  function validateClickOptions(args) {
    if (args.force !== undefined && typeof args.force !== "boolean")
      throw new Error("force must be a boolean");
    if (args.pointer !== undefined && typeof args.pointer !== "boolean")
      throw new Error("pointer must be a boolean");
    if (args.doubleClick !== undefined && typeof args.doubleClick !== "boolean")
      throw new Error("doubleClick must be a boolean");
    if (args.pointer && Array.isArray(args.modifiers) && args.modifiers.length)
      throw new Error("pointer click does not support keyboard modifiers; use native browser_click");
  }

  async function pointerClick(args) {
    const button = args.button || "left";
    if (!["left", "right", "middle"].includes(button))
      throw new Error("button must be left, right, or middle");
    if (Array.isArray(args.modifiers) && args.modifiers.length)
      throw new Error("pointer click does not support keyboard modifiers");
    const probe = await callTool("browser_evaluate", {
      element: args.element,
      target: args.target,
      function: `(element) => {
        if (!(element instanceof Element) || !element.isConnected) throw new Error("pointer target is detached");
        const disabled = element.matches(":disabled") || element.getAttribute("aria-disabled") === "true" || element.classList.contains("is-disabled");
        if (disabled) throw new Error("pointer target is disabled");
        element.scrollIntoView({ block: "center", inline: "center", behavior: "auto" });
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0 || rect.width <= 0 || rect.height <= 0)
          throw new Error("pointer target is not visible");
        const localX = rect.left + rect.width / 2;
        const localY = rect.top + rect.height / 2;
        const hit = document.elementFromPoint(localX, localY);
        let x = localX;
        let y = localY;
        let frameWindow = window;
        while (frameWindow && frameWindow !== frameWindow.parent) {
          const frameElement = frameWindow.frameElement;
          if (!frameElement) throw new Error("pointer target is inside a frame whose viewport offset is unavailable");
          const frameRect = frameElement.getBoundingClientRect();
          x += frameRect.left;
          y += frameRect.top;
          frameWindow = frameWindow.parent;
        }
        return {
          x, y,
          receivesPointer: hit === element || Boolean(hit && element.contains(hit)),
          hitTag: hit ? hit.tagName : null,
          tag: element.tagName,
          text: (element.innerText || element.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 200),
        };
      }`,
    });
    if (!probe || probe.isError === true)
      return probe;
    const state = evaluationValue(probe);
    if (!state || !Number.isFinite(state.x) || !Number.isFinite(state.y))
      return textResult("Pointer target did not return a usable viewport coordinate.", true);
    if (state.receivesPointer !== true)
      return textResult(`Pointer target is covered at its center by ${state.hitTag || "another element"}; no coordinate click was attempted.`, true);
    const clicked = await callTool("browser_mouse_click_xy", {
      x: state.x,
      y: state.y,
      button,
      clickCount: args.doubleClick === true ? 2 : 1,
    });
    if (!clicked || clicked.isError === true)
      return mergeResults(clicked || textResult("The vision pointer tool returned no result.", true), undefined,
        "Real pointer click was not completed; enable the vision capability and verify the target state before retrying.");
    return mergeResults(clicked, undefined,
      `Real pointer click completed at (${Math.round(state.x)}, ${Math.round(state.y)}) on ${state.tag}${state.text ? ` "${state.text}"` : ""}.`);
  }

  async function clipboard(args) {
    assertOnlyKeys(args, new Set(["element", "grantPermissions", "operation", "target", "text"]));
    const operation = requiredString(args, "operation");
    if (!["read", "write"].includes(operation))
      throw new Error("operation must be read or write");
    if (operation === "write" && typeof args.text !== "string")
      throw new Error("text is required when operation=write");
    if (args.grantPermissions !== undefined && typeof args.grantPermissions !== "boolean")
      throw new Error("grantPermissions must be a boolean");
    let permissionNote = "";
    if (args.grantPermissions === true) {
      try {
        const granted = await runCodeWithTimerShim({
          code: `async (page) => {
            let origin;
            try { origin = new URL(String(page.url())).origin; } catch { return { granted: false, reason: "opaque-origin" }; }
            if (!origin || origin === "null") return { granted: false, reason: "opaque-origin" };
            await page.context().grantPermissions([${JSON.stringify(operation === "read" ? "clipboard-read" : "clipboard-write")}], { origin });
            return { granted: true, origin };
          }`,
        });
        if (granted && granted.isError === true)
          permissionNote = " Browser permission grant was rejected; the operation was still attempted.";
      } catch {
        permissionNote = " Browser permission grant was unavailable; the operation was still attempted.";
      }
    }
    const expression = `(element) => (async () => {
      const secure = Boolean(window.isSecureContext);
      const available = Boolean(navigator.clipboard);
      if (!secure || !available) return { ok: false, reason: "secure-context-required", secureContext: secure, clipboardAvailable: available, url: location.href };
      try {
        ${operation === "write"
          ? `await navigator.clipboard.writeText(${JSON.stringify(args.text)}); return { ok: true, operation: "write" };`
          : `return { ok: true, operation: "read", text: await navigator.clipboard.readText() };`}
      } catch (error) {
        return { ok: false, reason: "permission-or-browser-error", message: String(error && error.message || error), url: location.href };
      }
    })()`;
    const evaluationArgs = { function: expression };
    if (args.target !== undefined)
      evaluationArgs.target = args.target;
    if (args.element !== undefined)
      evaluationArgs.element = args.element;
    const result = await callTool("browser_evaluate", evaluationArgs);
    if (!result || result.isError === true)
      return result;
    const value = evaluationValue(result);
    if (!value || value.ok !== true) {
      const reason = value && value.reason === "secure-context-required"
        ? "Clipboard is unavailable because this page is not a secure context (HTTPS/localhost) or the Clipboard API is missing."
        : `Clipboard operation failed: ${(value && value.message) || "permission denied or unsupported by the browser"}${permissionNote}`;
      return textResult(reason, true);
    }
    return operation === "read" ? textResult(value.text || "") : textResult(`Clipboard write completed.${permissionNote}`);
  }

  function textLocator(args) {
    const text = requiredString(args, "text");
    const exact = args.exact !== false;
    if (args.exact !== undefined && typeof args.exact !== "boolean")
      throw new Error("exact must be a boolean");
    if (args.role !== undefined && (typeof args.role !== "string" || !args.role.trim()))
      throw new Error("role must be a non-empty string when provided");
    if (args.pointer !== undefined && typeof args.pointer !== "boolean")
      throw new Error("pointer must be a boolean");
    if (args.role)
      return `getByRole(${JSON.stringify(args.role)}, { name: ${JSON.stringify(text)}, exact: ${exact} })`;
    return `getByText(${JSON.stringify(text)}, { exact: ${exact} })`;
  }

  function normalizeClickTextArgs(args) {
    const hasText = args && args.text !== undefined;
    if (!hasText) {
      if (args && (args.role !== undefined || args.exact !== undefined))
        throw new Error("role and exact require text");
      requiredString(args, "target");
      return args;
    }
    if (args.target !== undefined)
      throw new Error("provide either target or text for browser_click, not both");
    const target = textLocator(args);
    const normalized = {
      ...args,
      target,
      element: args.element || `visible text ${JSON.stringify(args.text)}`,
    };
    delete normalized.text;
    delete normalized.role;
    delete normalized.exact;
    return normalized;
  }

  async function runCodeWithTimerShim(args) {
    const next = { ...args };
    if (typeof next.code === "string")
      next.code = timerShimCode(next.code);
    return callTool("browser_run_code_unsafe", next);
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
    validateClickOptions(args);
    if (args.pointer === true)
      return { result: await pointerClick(args), fallback: true, pointer: true };
    const force = args.force === true;
    const native = await callTool("browser_click", clickArguments(args));
    if (!native || native.isError !== true)
      return { result: native, fallback: false };
    const detail = textFromResult(native);
    const unsupportedGesture = args.doubleClick === true || (args.button && args.button !== "left") ||
      (Array.isArray(args.modifiers) && args.modifiers.length > 0);
    if (unsupportedGesture || AMBIGUOUS_TARGET_FAILURE.test(detail) || INFRASTRUCTURE_FAILURE.test(detail) || !ACTIONABILITY_FAILURE.test(detail) ||
        (config.compatibilityMode !== "robust" && !force))
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
        force
          ? "Explicit force mode applied a guarded DOM click to the exact same target after native click failed. This is programmatic input; use pointer=true for trusted mouse events."
          : "Native Playwright click could not satisfy actionability checks; a guarded DOM click was applied to the exact same target."),
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

  async function handleToolCall(params, requestBeforeFiles, requestBeforeSequence = 0) {
    if (!params || typeof params !== "object" || typeof params.name !== "string")
      return textResult("tools/call requires a tool name", true);
    const name = params.name;
    const rawArgs = params.arguments && typeof params.arguments === "object" && !Array.isArray(params.arguments)
      ? params.arguments : {};
    try {
      const args = normalizeArtifactFilename(name, rawArgs, outputDirectory);
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
        const clicked = await guardedClick(normalizeClickTextArgs(args));
        return appendFreshSnapshot(
          clicked.result,
          "Click completed; fresh page snapshot:",
          clicked.fallback,
        );
      }

      if (name === "browser_click_pointer") {
        assertOnlyKeys(args, new Set(["button", "doubleClick", "element", "target"]));
        requiredString(args, "target");
        validateClickOptions({ ...args, pointer: true });
        const clicked = await guardedClick({ ...args, pointer: true });
        return appendFreshSnapshot(
          clicked.result,
          "Pointer click completed; fresh page snapshot:",
          true,
        );
      }

      if (name === "browser_click_text") {
        assertOnlyKeys(args, new Set(["doubleClick", "element", "exact", "force", "pointer", "role", "text"]));
        validateClickOptions(args);
        const target = textLocator(args);
        const clicked = await guardedClick({
          target,
          element: args.element || `visible text ${JSON.stringify(args.text)}`,
          force: args.force === true,
          pointer: args.pointer === true,
          doubleClick: args.doubleClick === true,
        });
        return appendFreshSnapshot(
          clicked.result,
          "Text locator click completed; fresh page snapshot:",
          true,
        );
      }

      if (name === "browser_clipboard")
        return clipboard(args);

      if (name === DOWNLOAD_WAIT_TOOL) {
        assertOnlyKeys(args, new Set(["timeoutMs"]));
        const timeoutMs = boundedInteger(args.timeoutMs, DOWNLOAD_MAX_WAIT_MS, 100, 120000);
        const pendingRecord = [...downloadRecords].reverse().find(item => item.status === "started");
        const before = pendingRecord ? Math.max(0, pendingRecord.sequence - 1) : requestBeforeSequence;
        const latestBeforeWait = [...downloadRecords].reverse().find(item => item.path && item.status === "finished");
        if (!pendingRecord && latestBeforeWait && downloadSequence <= requestBeforeSequence) {
          return {
            content: [{ type: "text", text: `Download already completed.\n${latestBeforeWait.path}` }],
            structuredContent: { artifacts: [{ path: latestBeforeWait.path, status: "finished", kind: "download" }] },
          };
        }
        const deadline = Date.now() + timeoutMs;
        let artifacts = [];
        while (Date.now() < deadline) {
          if (pendingRecord || downloadSequence > requestBeforeSequence) {
            await waitForDownloadWindow(before, Math.max(0, deadline - Date.now()), requestBeforeFiles);
            artifacts = recentDownloadArtifacts(before);
            if (artifacts.length)
              break;
          }
          const changed = requestBeforeFiles
            ? changedOutputArtifacts(requestBeforeFiles, outputDirectory, DOWNLOAD_WAIT_TOOL)
            : [];
          if (changed.length) {
            attachDownloadArtifacts(before, changed);
            artifacts = [...recentDownloadArtifacts(before), ...changed]
              .filter((item, index, all) => item && item.path && all.findIndex(other => other && other.path === item.path) === index);
            break;
          }
          await new Promise(resolve => setTimeout(resolve, 50));
        }
        if (!artifacts.length) {
          const latest = [...downloadRecords].reverse().find(item => item.path && item.status === "finished");
          if (latest && !pendingRecord && downloadSequence <= requestBeforeSequence)
            artifacts = [{ path: latest.path, status: "finished", kind: "download" }];
        }
        if (!artifacts.length) {
          const timedOut = downloadRecords.some(item => item.sequence > before && item.status === "timeout");
          if (timedOut)
            return textResult("A browser download was observed but did not finish before the timeout, and no safe output path was reported.", true);
          return textResult("No pending browser download was observed, or the output path was not reported before the timeout.", true);
        }
        if (artifacts.some(item => item.status === "timeout")) {
          return {
            isError: true,
            content: [{ type: "text", text: `A browser download was observed but did not finish before the timeout.\n${artifacts.map(item => item.path).join("\n")}` }],
            structuredContent: { artifacts },
          };
        }
        return {
          content: [{ type: "text", text: `Download completed.\n${artifacts.map(item => item.path).join("\n")}` }],
          structuredContent: { artifacts },
        };
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
        const options = waitOptions(args, Math.max(config.settleMs * 6, 5000));
        const clickArgs = normalizeClickTextArgs(stripWaitOptions(args));
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

      if (name === "browser_run_code_unsafe")
        return runCodeWithTimerShim(args);

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
      const params = message.params || {};
      const name = typeof params.name === "string" ? params.name : "";
      const rawArgs = params.arguments && typeof params.arguments === "object" && !Array.isArray(params.arguments)
        ? params.arguments : {};
      const args = { ...rawArgs };
      const requestMetaValues = [message._meta, params._meta, args._meta]
        .filter(value => value && typeof value === "object" && !Array.isArray(value));
      const requestMeta = requestMetaValues.length
        ? Object.assign({}, ...requestMetaValues)
        : undefined;
      delete args._meta;
      const beforeDownloadSequence = downloadSequence;
      const capturesOutput = ARTIFACT_TOOLS.has(name) &&
        (!['browser_evaluate', 'browser_console_messages', 'browser_network_request', 'browser_network_requests'].includes(name) ||
          typeof args.filename === "string");
      const observesDownload = DOWNLOAD_TRIGGER_TOOLS.has(name) || name === DOWNLOAD_WAIT_TOOL;
      const beforeOutputFiles = (capturesOutput || observesDownload)
        ? outputFiles(outputDirectory)
        : undefined;
      const previousMeta = activeRequestMeta;
      const previousWorkspace = activeRequestWorkspace;
      activeRequestMeta = requestMeta && typeof requestMeta === "object" && !Array.isArray(requestMeta)
        ? requestMeta
        : undefined;
      const requestedWorkspace = localWorkspacePath(activeRequestMeta && activeRequestMeta.cwd, clientWorkspace);
      activeRequestWorkspace = requestedWorkspace;
      try {
        const toolParams = { ...params, arguments: args };
        let result = await handleToolCall(toolParams, beforeOutputFiles, beforeDownloadSequence);
        // The wrapper's own wait result says "Download completed"; feeding it
        // back into the event parser would manufacture a duplicate record.
        // Upstream action/snapshot text and notifications are still parsed.
        if (name !== DOWNLOAD_WAIT_TOOL)
          recordDownloadText(textFromResult(result));
        if (DOWNLOAD_TRIGGER_TOOLS.has(name))
          await waitForDownloadWindow(beforeDownloadSequence, DOWNLOAD_MAX_WAIT_MS, beforeOutputFiles);
        if (DOWNLOAD_TRIGGER_TOOLS.has(name) && downloadRecords.some(item => item.sequence > beforeDownloadSequence && item.status === "timeout"))
          result = mergeResults(result, undefined,
            "A browser download was observed but did not finish within the bounded wait window. Use browser_wait_for_download to inspect its status before retrying.");
        const observedDownload = downloadSequence > beforeDownloadSequence;
        const changedArtifacts = beforeOutputFiles &&
          (capturesOutput || observedDownload || name === DOWNLOAD_WAIT_TOOL)
          ? changedOutputArtifacts(beforeOutputFiles, outputDirectory, name)
          : [];
        if (observesDownload)
          attachDownloadArtifacts(beforeDownloadSequence, changedArtifacts);
        return { result: decorateArtifacts(result, name, args, beforeDownloadSequence, changedArtifacts) };
      } finally {
        activeRequestMeta = previousMeta;
        activeRequestWorkspace = previousWorkspace;
      }
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
    if (pendingRootRequests.has(message.id)) {
      pendingRootRequests.delete(message.id);
      const roots = message.result && Array.isArray(message.result.roots) ? message.result.roots : [];
      for (const root of roots) {
        if (!root || typeof root.uri !== "string")
          continue;
        try {
          const parsed = new URL(root.uri);
          if (parsed.protocol !== "file:")
            continue;
          const rootPath = fileURLToPath(parsed);
          if (path.isAbsolute(rootPath)) {
            clientWorkspace = path.resolve(rootPath);
            break;
          }
        } catch {
          // Ignore non-local or malformed roots; the upstream default cwd remains safe.
        }
      }
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
  let settings;
  try {
    config = loadInteractionConfig(args);
    settings = loadPlaywrightSettings(args);
  } catch (error) {
    failStartup(error instanceof Error ? error.message : String(error));
    return;
  }
  runProxy(upstreamCli, args, config, settings);
}

main();
