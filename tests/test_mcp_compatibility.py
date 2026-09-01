from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "runtime" / "bin" / "intranet-browser-agent-mcp.js"
NODE = shutil.which("node") or shutil.which("node.exe")


FAKE_UPSTREAM = r'''"use strict";
const fs = require("fs");
const readline = require("readline");
let snapshotCount = 0;
const logPath = process.env.FAKE_CALL_LOG;
function send(value) { process.stdout.write(JSON.stringify(value) + "\n"); }
function result(id, value) { send({ jsonrpc: "2.0", id, result: value }); }
function toolResult(text, isError = false) {
  return { content: text === null ? [] : [{ type: "text", text }], ...(isError ? { isError: true } : {}) };
}
const tools = [
  { name: "browser_navigate", description: "Navigate", inputSchema: { type: "object", required: ["url"], properties: { url: { type: "string" } }, additionalProperties: false } },
  { name: "browser_snapshot", description: "Snapshot", inputSchema: { type: "object", properties: { target: { type: "string" }, filename: { type: "string" }, depth: { type: "number" }, boxes: { type: "boolean" } }, additionalProperties: false } },
  { name: "browser_click", description: "Click", inputSchema: { type: "object", required: ["target"], properties: { target: { type: "string" }, element: { type: "string" } }, additionalProperties: false } },
  { name: "browser_type", description: "Type", inputSchema: { type: "object", required: ["target", "text"], properties: { target: { type: "string" }, text: { type: "string" } }, additionalProperties: false } },
  { name: "browser_evaluate", description: "Evaluate", inputSchema: { type: "object", required: ["function"], properties: { function: { type: "string" }, target: { type: "string" }, element: { type: "string" } }, additionalProperties: false } },
  { name: "browser_wait_for", description: "Wait", inputSchema: { type: "object", properties: {} } },
];
const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
lines.on("line", line => {
  const message = JSON.parse(line);
  if (!Object.prototype.hasOwnProperty.call(message, "id")) return;
  if (message.method === "initialize") {
    result(message.id, { protocolVersion: "2025-03-26", capabilities: { tools: {} }, serverInfo: { name: "Playwright", version: "fake" } });
    return;
  }
  if (message.method === "tools/list") { result(message.id, { tools }); return; }
  if (message.method !== "tools/call") { result(message.id, {}); return; }
  const name = message.params.name;
  const args = message.params.arguments || {};
  if (logPath) fs.appendFileSync(logPath, JSON.stringify({ name, args }) + "\n");
  if (name === "browser_snapshot") {
    snapshotCount += 1;
    if (process.env.FAKE_EMPTY_FIRST === "1" && snapshotCount === 1) result(message.id, toolResult(null));
    else if (process.env.FAKE_LARGE_SNAPSHOT === "1") result(message.id, toolResult("X".repeat(30000)));
    else result(message.id, toolResult(`SNAPSHOT depth=${args.depth} boxes=${args.boxes} filename=${args.filename || "inline"}`));
    return;
  }
  if (name === "browser_navigate") { result(message.id, toolResult("NAVIGATED")); return; }
  if (name === "browser_click") {
    const error = process.env.FAKE_CLICK_ERROR || "";
    if (error) result(message.id, toolResult(error, true));
    else result(message.id, toolResult("NATIVE CLICK"));
    return;
  }
  if (name === "browser_type") { result(message.id, toolResult("NATIVE TYPE")); return; }
  if (name === "browser_evaluate") {
    const expression = String(args.function || "");
    let value = { ok: true };
    if (expression.includes("return { tag, role, readonly")) {
      value = JSON.parse(process.env.FAKE_FIELD_STATE || '{"readonly":false,"disabled":false,"customSelect":false,"editable":true}');
    } else if (expression.includes("const wanted")) {
      value = { selected: "chosen", exact: true };
    } else if (expression.includes("const described")) {
      value = { text: "FULL TOOLTIP TEXT", source: "visible-tooltip" };
    } else if (expression.includes("guarded-same-target-dom-click")) {
      value = { clicked: true, fallback: "guarded-same-target-dom-click" };
    } else if (expression.includes("page condition was not met")) {
      value = { met: true, url: "http://fixture/page/2" };
    } else if (expression.includes("MutationObserver")) {
      value = { reason: "settled", elapsedMs: 1500 };
    }
    result(message.id, toolResult("```json\n" + JSON.stringify(value) + "\n```"));
    return;
  }
  result(message.id, toolResult("UPSTREAM " + name));
});
'''


class McpClient:
    def __init__(self, command: list[str], environment: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
        assert self.process.stdin and self.process.stdout and self.process.stderr
        self.messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self.stderr: list[str] = []
        self.stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self.stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self.stdout_thread.start()
        self.stderr_thread.start()
        self.sequence = 0

    def _read_stdout(self) -> None:
        assert self.process.stdout
        try:
            for line in self.process.stdout:
                if line.strip():
                    self.messages.put(json.loads(line))
        except BaseException as exc:
            self.messages.put(exc)
        finally:
            self.messages.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr
        self.stderr.extend(line.rstrip() for line in self.process.stderr)

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any], timeout: float = 10) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"timed out; stderr={' | '.join(self.stderr)}")
            message = self.messages.get(timeout=remaining)
            if message is None:
                raise AssertionError(f"MCP exited; stderr={' | '.join(self.stderr)}")
            if isinstance(message, BaseException):
                raise message
            if message.get("id") == request_id:
                if "error" in message:
                    raise AssertionError(message["error"])
                return message["result"]

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        assert self.process.stdin
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.stdout_thread.join(timeout=2)
        self.stderr_thread.join(timeout=2)
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()


@unittest.skipUnless(NODE, "Node.js is required for compatibility-layer protocol tests")
class McpCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        runtime = self.root / "runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "node_modules" / "@playwright" / "mcp").mkdir(parents=True)
        shutil.copy2(WRAPPER, runtime / "bin" / WRAPPER.name)
        (runtime / "node_modules" / "@playwright" / "mcp" / "cli.js").write_bytes(
            FAKE_UPSTREAM.encode("utf-8")
        )
        self.config_root = self.root / "config"
        self.config_root.mkdir()
        self.playwright_config = self.config_root / "playwright.config.json"
        self.playwright_config.write_bytes(b"{}\n")
        self.wrapper = runtime / "bin" / WRAPPER.name
        self.log = self.root / "calls.jsonl"
        self.write_interaction("compact", "robust")
        self.client: McpClient | None = None

    def tearDown(self) -> None:
        if self.client:
            self.client.close()
        self.temporary.cleanup()

    def write_interaction(self, snapshot: str, compatibility: str) -> None:
        payload = {
            "schemaVersion": 1,
            "snapshotStrategy": snapshot,
            "compatibilityMode": compatibility,
            "defaultSnapshotDepth": 6,
            "settleMs": 1500,
        }
        (self.config_root / "interaction.config.json").write_bytes(
            (json.dumps(payload) + "\n").encode("utf-8")
        )

    def start(self, **environment: str) -> McpClient:
        child_environment = dict(os.environ)
        child_environment.update(environment)
        child_environment["FAKE_CALL_LOG"] = str(self.log)
        self.client = McpClient(
            [str(NODE), str(self.wrapper), "--config", str(self.playwright_config)],
            child_environment,
        )
        initialized = self.client.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "compat-test", "version": "1"},
            },
        )
        self.assertEqual("Playwright", initialized["serverInfo"]["name"])
        self.client.send(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        return self.client

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert self.client
        return self.client.request(
            "tools/call", {"name": name, "arguments": arguments}
        )

    def test_tools_list_exposes_compatibility_tools_and_navigation_waits(self) -> None:
        client = self.start()
        listed = client.request("tools/list", {})
        by_name = {tool["name"]: tool for tool in listed["tools"]}
        self.assertIn("browser_select_custom_option", by_name)
        self.assertIn("browser_read_tooltip", by_name)
        self.assertIn("browser_click_and_wait", by_name)
        self.assertIn(
            "waitForSelector",
            by_name["browser_navigate"]["inputSchema"]["properties"],
        )

    def test_missing_interaction_config_fails_closed(self) -> None:
        (self.config_root / "interaction.config.json").unlink()
        result = subprocess.run(
            [str(NODE), str(self.wrapper), "--config", str(self.playwright_config)],
            input="",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("interaction config is missing", result.stderr)

    def test_navigation_waits_and_retries_empty_snapshot_inline(self) -> None:
        self.start(FAKE_EMPTY_FIRST="1")
        result = self.call_tool("browser_navigate", {"url": "http://fixture/"})
        rendered = json.dumps(result)
        self.assertIn("NAVIGATED", rendered)
        self.assertIn("SNAPSHOT depth=6", rendered)
        calls = self.calls()
        self.assertEqual("browser_navigate", calls[0]["name"])
        self.assertGreaterEqual(
            sum(item["name"] == "browser_snapshot" for item in calls), 2
        )
        self.assertTrue(
            any(
                item["name"] == "browser_evaluate"
                and "MutationObserver" in item["args"].get("function", "")
                for item in calls
            )
        )
        snapshot_calls = [item for item in calls if item["name"] == "browser_snapshot"]
        self.assertTrue(all("filename" not in item["args"] for item in snapshot_calls))

    def test_compact_snapshot_has_a_hard_inline_size_bound(self) -> None:
        self.start(FAKE_LARGE_SNAPSHOT="1")
        result = self.call_tool("browser_snapshot", {})
        rendered = json.dumps(result)
        self.assertLess(len(rendered), 17000)
        self.assertIn("truncated at 16000 characters", rendered)
        snapshot_call = next(
            item for item in self.calls() if item["name"] == "browser_snapshot"
        )
        self.assertEqual(6, snapshot_call["args"]["depth"])
        self.assertFalse(snapshot_call["args"]["boxes"])

    def test_compact_snapshot_explicit_filename_preserves_full_request(self) -> None:
        self.start()
        result = self.call_tool(
            "browser_snapshot", {"filename": "evidence.md", "target": "ref=table"}
        )
        self.assertIn("filename=evidence.md", json.dumps(result))
        snapshot_call = next(
            item for item in self.calls() if item["name"] == "browser_snapshot"
        )
        self.assertEqual(
            {"filename": "evidence.md", "target": "ref=table"},
            snapshot_call["args"],
        )

    def test_native_click_success_does_not_use_dom_fallback(self) -> None:
        self.start()
        result = self.call_tool("browser_click", {"target": "ref=button"})
        self.assertIn("NATIVE CLICK", json.dumps(result))
        self.assertFalse(
            any(
                "guarded-same-target-dom-click" in item["args"].get("function", "")
                for item in self.calls()
                if item["name"] == "browser_evaluate"
            )
        )

    def test_actionability_click_failure_uses_guarded_same_target_fallback(self) -> None:
        self.start(
            FAKE_CLICK_ERROR=(
                "Timeout 5000ms exceeded waiting for element to be visible, enabled and stable"
            )
        )
        result = self.call_tool("browser_click", {"target": "ref=same-button"})
        self.assertNotEqual(True, result.get("isError"))
        fallback = next(
            item
            for item in self.calls()
            if item["name"] == "browser_evaluate"
            and "guarded-same-target-dom-click" in item["args"].get("function", "")
        )
        self.assertEqual("ref=same-button", fallback["args"]["target"])

    def test_non_actionability_click_error_does_not_fallback(self) -> None:
        self.start(
            FAKE_CLICK_ERROR=(
                "Timeout 5000ms exceeded: strict mode violation: resolved to 2 elements"
            )
        )
        result = self.call_tool("browser_click", {"target": "button"})
        self.assertTrue(result["isError"])
        self.assertFalse(
            any(
                "guarded-same-target-dom-click" in item["args"].get("function", "")
                for item in self.calls()
                if item["name"] == "browser_evaluate"
            )
        )

    def test_readonly_plain_field_fails_before_native_fill(self) -> None:
        self.start(
            FAKE_FIELD_STATE=json.dumps(
                {
                    "readonly": True,
                    "disabled": False,
                    "customSelect": False,
                    "editable": False,
                }
            )
        )
        result = self.call_tool(
            "browser_type", {"target": "ref=readonly", "text": "value"}
        )
        self.assertTrue(result["isError"])
        self.assertIn("readonly", json.dumps(result).lower())
        self.assertFalse(any(item["name"] == "browser_type" for item in self.calls()))

    def test_readonly_custom_combobox_selects_visible_option(self) -> None:
        self.start(
            FAKE_FIELD_STATE=json.dumps(
                {
                    "readonly": True,
                    "disabled": False,
                    "customSelect": True,
                    "editable": False,
                }
            )
        )
        result = self.call_tool(
            "browser_type", {"target": "ref=select", "text": "生产"}
        )
        self.assertNotEqual(True, result.get("isError"))
        self.assertTrue(
            any(
                item["name"] == "browser_evaluate"
                and "const wanted" in item["args"].get("function", "")
                and item["args"]["target"] == "ref=select"
                for item in self.calls()
            )
        )
        self.assertFalse(any(item["name"] == "browser_type" for item in self.calls()))

    def test_tooltip_returns_untruncated_text_inline(self) -> None:
        self.start()
        result = self.call_tool("browser_read_tooltip", {"target": "ref=log"})
        self.assertEqual("FULL TOOLTIP TEXT", result["content"][0]["text"])

    def test_click_and_wait_verifies_condition_before_snapshot(self) -> None:
        self.start()
        result = self.call_tool(
            "browser_click_and_wait",
            {"target": "ref=page2", "waitForText": "Page 2", "timeoutMs": 3000},
        )
        self.assertIn("postcondition verified", json.dumps(result))
        calls = self.calls()
        click_index = next(index for index, item in enumerate(calls) if item["name"] == "browser_click")
        condition_index = next(
            index
            for index, item in enumerate(calls)
            if item["name"] == "browser_evaluate"
            and "page condition was not met" in item["args"].get("function", "")
        )
        snapshot_index = next(index for index, item in enumerate(calls) if item["name"] == "browser_snapshot")
        self.assertLess(click_index, condition_index)
        self.assertLess(condition_index, snapshot_index)

    def test_full_click_and_wait_takes_fresh_snapshot_after_condition(self) -> None:
        self.write_interaction("full", "standard")
        self.start()
        result = self.call_tool(
            "browser_click_and_wait",
            {"target": "ref=page2", "waitForText": "Page 2", "timeoutMs": 3000},
        )
        self.assertIn("postcondition verified", json.dumps(result))
        calls = self.calls()
        condition_index = next(
            index
            for index, item in enumerate(calls)
            if item["name"] == "browser_evaluate"
            and "page condition was not met" in item["args"].get("function", "")
        )
        snapshot_index = next(
            index for index, item in enumerate(calls) if item["name"] == "browser_snapshot"
        )
        self.assertLess(condition_index, snapshot_index)

    def test_full_standard_mode_preserves_upstream_snapshot_and_click_error(self) -> None:
        self.write_interaction("full", "standard")
        self.start(FAKE_CLICK_ERROR="Timeout waiting for element to be visible")
        click = self.call_tool("browser_click", {"target": "ref=button"})
        self.assertTrue(click["isError"])
        snapshot = self.call_tool("browser_snapshot", {})
        self.assertIn("depth=undefined", json.dumps(snapshot))
        calls = self.calls()
        self.assertEqual(1, sum(item["name"] == "browser_snapshot" for item in calls))
        self.assertFalse(
            any(
                "guarded-same-target-dom-click" in item["args"].get("function", "")
                for item in calls
                if item["name"] == "browser_evaluate"
            )
        )


if __name__ == "__main__":
    unittest.main()
