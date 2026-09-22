"""Command-line entry points for the shared MCP standards package."""

from __future__ import annotations

import argparse
import json
import sys

from .claude_code import discover_claude
from .stdio import SmokeError, smoke_stdio


def _discover(args: argparse.Namespace) -> int:
    result = discover_claude(explicit_path=args.explicit_path, probe=not args.no_probe)
    payload = {
        "selected": None,
        "diagnostics": [diagnostic.__dict__ for diagnostic in result.diagnostics],
    }
    if result.selected:
        selected = result.selected
        payload["selected"] = {
            "commandPath": str(selected.command_path),
            "executable": str(selected.executable),
            "prefix": list(selected.prefix),
            "kind": selected.kind,
            "source": selected.source,
            "finalPath": str(selected.final_path),
            "versionExitCode": selected.version.exit_code if selected.version else None,
            "capabilityExitCode": selected.capability.exit_code if selected.capability else None,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.selected else 2


def _smoke(args: argparse.Namespace) -> int:
    command = list(args.command_args)
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        report = smoke_stdio(
            command,
            expected_server_name=args.expected_server_name,
            required_tools=args.required_tool,
            timeout=args.timeout,
        )
    except SmokeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "protocolVersion": report.protocol_version,
        "serverName": report.server_name,
        "serverVersion": report.server_version,
        "toolCount": len(report.tool_names),
        "tools": list(report.tool_names),
    }, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mcp-standards")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)
    discover = subparsers.add_parser("discover-claude")
    discover.add_argument("--explicit-path")
    discover.add_argument("--no-probe", action="store_true")
    discover.set_defaults(handler=_discover)
    smoke = subparsers.add_parser("smoke-stdio")
    smoke.add_argument("--expected-server-name", required=True)
    smoke.add_argument("--required-tool", action="append", default=[])
    smoke.add_argument("--timeout", type=float, default=20.0)
    smoke.add_argument("command_args", nargs=argparse.REMAINDER)
    smoke.set_defaults(handler=_smoke)
    args = parser.parse_args(argv)
    if args.subcommand == "smoke-stdio" and not args.command_args:
        parser.error("smoke-stdio requires a command after --")
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
