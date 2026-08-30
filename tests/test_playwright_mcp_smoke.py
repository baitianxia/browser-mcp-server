from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "smoke_playwright_mcp", ROOT / "scripts" / "smoke_playwright_mcp.py"
)
assert SPEC and SPEC.loader
smoke_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke_module)


FAKE_SERVER = textwrap.dedent(
    """
    import json
    import os
    import sys

    if os.environ.get("PLAYWRIGHT_MCP_CONFIG") != "":
        print("inherited Playwright MCP config was not cleared", file=sys.stderr)
        raise SystemExit(10)
    if os.environ.get("NODE_OPTIONS") != "":
        print("inherited NODE_OPTIONS was not cleared", file=sys.stderr)
        raise SystemExit(11)
    expected_browser = os.environ.get("EXPECT_BROWSER")
    if expected_browser and f"--browser={expected_browser}" not in sys.argv[1:]:
        print("expected browser channel was not forwarded", file=sys.stderr)
        raise SystemExit(12)
    expected_executable = os.environ.get("EXPECT_BROWSER_EXECUTABLE")
    if expected_executable and f"--executable-path={expected_executable}" not in sys.argv[1:]:
        print("expected browser executable was not forwarded", file=sys.stderr)
        raise SystemExit(13)

    for line in sys.stdin:
        message = json.loads(line)
        if message.get("id") == 1:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "Playwright", "version": "fixture"},
                },
            }), flush=True)
        elif message.get("id") == 2:
            names = [] if os.environ.get("EMPTY_TOOLS") else [
                "browser_navigate", "browser_snapshot"
            ]
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [{"name": name} for name in names]},
            }), flush=True)
    """
).lstrip()


class PlaywrightMcpSmokeTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path]:
        server = root / "fake_server.py"
        server.write_bytes(FAKE_SERVER.encode("utf-8"))
        config = root / "playwright.config.json"
        config.write_bytes(json.dumps({}).encode("utf-8") + b"\n")
        return server, config

    def test_real_subprocess_stdio_handshake(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server, config = self.fixture(Path(temporary))
            version, tool_count = smoke_module.smoke(
                Path(sys.executable), server, config
            )
            self.assertEqual("fixture", version)
            self.assertEqual(2, tool_count)

    def test_empty_tool_list_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server, config = self.fixture(Path(temporary))
            with mock.patch.dict("os.environ", {"EMPTY_TOOLS": "1"}):
                with self.assertRaises(smoke_module.SmokeError):
                    smoke_module.smoke(Path(sys.executable), server, config)

    def test_inherited_playwright_and_node_overrides_are_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server, config = self.fixture(Path(temporary))
            with mock.patch.dict(
                "os.environ",
                {
                    "PLAYWRIGHT_MCP_CONFIG": "C:\\hostile\\override.json",
                    "NODE_OPTIONS": "--require=unexpected.js",
                },
            ):
                version, tool_count = smoke_module.smoke(
                    Path(sys.executable), server, config
                )
            self.assertEqual("fixture", version)
            self.assertEqual(2, tool_count)

    def test_extension_browser_channel_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server, config = self.fixture(root)
            browser = root / "chrome.exe"
            browser.write_bytes(b"MZ")
            with mock.patch.dict(
                "os.environ",
                {
                    "EXPECT_BROWSER": "chrome",
                    "EXPECT_BROWSER_EXECUTABLE": str(browser),
                },
            ):
                version, tool_count = smoke_module.smoke(
                    Path(sys.executable),
                    server,
                    config,
                    browser_channel="chrome",
                    browser_executable=browser,
                )
            self.assertEqual("fixture", version)
            self.assertEqual(2, tool_count)


if __name__ == "__main__":
    unittest.main()
