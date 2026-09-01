#!/usr/bin/env python3
"""Generate a user-scoped Windows pilot configuration without touching projects."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import browser_agent  # noqa: E402


class ConfiguratorError(RuntimeError):
    pass


def user_path_errors(local_app_data: Path, paths: list[Path]) -> list[str]:
    if not local_app_data.is_dir():
        return [f"LOCALAPPDATA is not a directory: {local_app_data}"]
    try:
        root_absolute = os.path.normcase(os.path.abspath(local_app_data))
        root = os.path.normcase(os.path.realpath(local_app_data))
    except OSError as exc:
        return [f"cannot resolve LOCALAPPDATA: {exc}"]
    errors: list[str] = []
    for path in paths:
        try:
            candidate_absolute = os.path.normcase(os.path.abspath(path))
            if os.path.commonpath((candidate_absolute, root_absolute)) != root_absolute:
                errors.append(f"path is outside LOCALAPPDATA: {path}")
                continue
            relative = os.path.relpath(candidate_absolute, root_absolute)
            expected = os.path.normcase(os.path.join(root, relative))
            candidate = os.path.normcase(os.path.realpath(path))
            if os.path.commonpath((candidate, root)) != root or candidate == root:
                errors.append(f"path escapes LOCALAPPDATA after resolving links: {path}")
            elif candidate != expected:
                errors.append(f"path traverses a link/junction below LOCALAPPDATA: {path}")
        except (OSError, ValueError) as exc:
            errors.append(f"cannot resolve user path {path}: {exc}")
    return errors


def assert_user_paths(args: argparse.Namespace) -> None:
    errors = user_path_errors(args.local_app_data, args.paths)
    if errors:
        raise ConfiguratorError("user path validation failed:\n- " + "\n- ".join(errors))
    print("VALID USER PATHS")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_manifest(
    template: dict[str, Any],
    *,
    runtime_root: str,
    config_root: str,
    output_directory: str,
    profile_owner: str,
    browser_channel: str,
    browser_executable: str,
    node_executable: str,
    browser_mode: str = "extension",
    headless: bool = False,
    extension_authorization: str = "session",
    user_data_dir: str | None = None,
) -> dict[str, Any]:
    manifest = copy.deepcopy(template)
    if manifest.get("environment") != "pilot":
        raise ConfiguratorError("the installer accepts only a pilot template")
    if manifest.get("target") != {"os": "windows", "arch": "x64"}:
        raise ConfiguratorError("the installer accepts only a Windows x64 template")

    # The template's relative editor hint points into the toolkit tree. It would
    # be broken after the generated manifest is moved into LOCALAPPDATA.
    manifest.pop("$schema", None)
    manifest["mcpScope"] = "user"
    manifest["installRoot"] = runtime_root
    manifest["nodeExecutable"] = node_executable
    manifest["configRoot"] = config_root
    manifest["workspaceRoots"] = []
    manifest["output"]["directory"] = output_directory
    manifest["browser"].update(
        {
            "channel": browser_channel,
            "executablePath": browser_executable,
            "profileOwner": profile_owner,
        }
    )
    apply_browser_preferences(
        manifest,
        browser_mode=browser_mode,
        headless=headless,
        extension_authorization=extension_authorization,
        user_data_dir=user_data_dir,
    )
    manifest.pop("network", None)
    manifest.pop("dataBoundary", None)
    errors = browser_agent.validate_manifest(manifest)
    if errors:
        raise ConfiguratorError("manifest validation failed:\n- " + "\n- ".join(errors))
    return manifest


def apply_browser_preferences(
    manifest: dict[str, Any],
    *,
    browser_mode: str,
    headless: bool,
    extension_authorization: str,
    user_data_dir: str | None,
) -> None:
    """Apply only supported Windows pilot browser-mode combinations."""
    if browser_mode not in {"extension", "dedicated"}:
        raise ConfiguratorError("browser mode must be extension or dedicated")
    if extension_authorization not in {"session", "user"}:
        raise ConfiguratorError(
            "extension authorization must be session or user"
        )
    browser = manifest.get("browser")
    if not isinstance(browser, dict):
        raise ConfiguratorError("manifest browser settings are missing")

    if browser_mode == "extension":
        if headless:
            raise ConfiguratorError(
                "headless mode cannot be combined with the browser extension"
            )
        manifest["mode"] = "extension"
        browser.pop("userDataDir", None)
        browser.pop("headless", None)
        browser["extensionDistribution"] = "manual-pilot"
        browser["manualConnectionApproval"] = (
            extension_authorization == "session"
        )
        return

    if not user_data_dir:
        raise ConfiguratorError("dedicated browser mode requires a user data directory")
    manifest["mode"] = "persistent"
    browser["userDataDir"] = user_data_dir
    browser["headless"] = headless
    browser.pop("extensionDistribution", None)
    browser.pop("manualConnectionApproval", None)


def reconfigure(args: argparse.Namespace) -> None:
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfiguratorError(f"cannot read installed pilot manifest: {exc}") from exc
    if manifest.get("environment") != "pilot" or manifest.get("mcpScope") != "user":
        raise ConfiguratorError(
            "only an installed user-scoped pilot manifest can be reconfigured"
        )
    if manifest.get("target") != {"os": "windows", "arch": "x64"}:
        raise ConfiguratorError("only a Windows x64 pilot can be reconfigured")
    apply_browser_preferences(
        manifest,
        browser_mode=args.browser_mode,
        headless=args.headless == "true",
        extension_authorization=args.extension_authorization,
        user_data_dir=args.user_data_dir,
    )
    errors = browser_agent.validate_manifest(manifest)
    if errors:
        raise ConfiguratorError("manifest validation failed:\n- " + "\n- ".join(errors))
    if args.manifest_out.exists() and not args.force:
        raise ConfiguratorError(f"refusing to overwrite {args.manifest_out}; pass --force")
    atomic_write_text(
        args.manifest_out,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    browser_agent.render(args.manifest_out, args.render_out, args.force)


def generate(args: argparse.Namespace) -> None:
    try:
        template = json.loads(args.template.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfiguratorError(f"cannot read pilot template: {exc}") from exc
    manifest = build_manifest(
        template,
        runtime_root=args.runtime_root,
        config_root=args.config_root,
        output_directory=args.output_directory,
        profile_owner=args.profile_owner,
        browser_channel=args.browser_channel,
        browser_executable=args.browser_executable,
        node_executable=args.node_executable,
        browser_mode=getattr(args, "browser_mode", "extension"),
        headless=getattr(args, "headless", "false") == "true",
        extension_authorization=getattr(
            args, "extension_authorization", "session"
        ),
        user_data_dir=getattr(args, "user_data_dir", None),
    )
    if args.manifest_out.exists() and not args.force:
        raise ConfiguratorError(f"refusing to overwrite {args.manifest_out}; pass --force")
    atomic_write_text(
        args.manifest_out,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    browser_agent.render(args.manifest_out, args.render_out, args.force)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--template", required=True, type=Path)
    generate_parser.add_argument("--manifest-out", required=True, type=Path)
    generate_parser.add_argument("--render-out", required=True, type=Path)
    generate_parser.add_argument("--runtime-root", required=True)
    generate_parser.add_argument("--config-root", required=True)
    generate_parser.add_argument("--output-directory", required=True)
    generate_parser.add_argument("--profile-owner", required=True)
    generate_parser.add_argument("--node-executable", required=True)
    generate_parser.add_argument(
        "--browser-channel", required=True, choices=("chrome", "msedge")
    )
    generate_parser.add_argument("--browser-executable", required=True)
    generate_parser.add_argument(
        "--browser-mode", choices=("extension", "dedicated"), default="extension"
    )
    generate_parser.add_argument(
        "--headless", choices=("true", "false"), default="false"
    )
    generate_parser.add_argument(
        "--extension-authorization",
        choices=("session", "user"),
        default="session",
    )
    generate_parser.add_argument("--user-data-dir")
    generate_parser.add_argument("--force", action="store_true")
    reconfigure_parser = subparsers.add_parser("reconfigure")
    reconfigure_parser.add_argument("--manifest", required=True, type=Path)
    reconfigure_parser.add_argument("--manifest-out", required=True, type=Path)
    reconfigure_parser.add_argument("--render-out", required=True, type=Path)
    reconfigure_parser.add_argument(
        "--browser-mode", required=True, choices=("extension", "dedicated")
    )
    reconfigure_parser.add_argument(
        "--headless", required=True, choices=("true", "false")
    )
    reconfigure_parser.add_argument(
        "--extension-authorization",
        required=True,
        choices=("session", "user"),
    )
    reconfigure_parser.add_argument("--user-data-dir")
    reconfigure_parser.add_argument("--force", action="store_true")
    paths_parser = subparsers.add_parser("assert-user-paths")
    paths_parser.add_argument("--local-app-data", required=True, type=Path)
    paths_parser.add_argument("--path", action="append", required=True, dest="paths", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "generate":
            generate(args)
        elif args.command == "reconfigure":
            reconfigure(args)
        else:
            assert_user_paths(args)
    except (ConfiguratorError, browser_agent.ManifestError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
