#!/usr/bin/env python3
"""Generate a user-scoped Windows pilot configuration without touching projects."""

from __future__ import annotations

import argparse
import copy
import json
import ntpath
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import browser_agent  # noqa: E402


class ConfiguratorError(RuntimeError):
    pass


WINDOWS_RUNTIME_DIRECTORY_RE = re.compile(
    r"^browser-agent-runtime-\d+\.\d+\.\d+-core-windows-x64$"
)


def _windows_path_key(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or not ntpath.isabs(value):
        raise ConfiguratorError(f"{label} must be an absolute Windows path")
    return ntpath.normcase(ntpath.normpath(value))


def _require_exact_windows_path(value: object, expected: str, label: str) -> None:
    if _windows_path_key(value, label) != _windows_path_key(expected, label):
        raise ConfiguratorError(f"{label} does not match the managed pilot path")


def _require_windows_path_within(value: object, parent: str, label: str) -> str:
    candidate = _windows_path_key(value, label)
    root = _windows_path_key(parent, label)
    try:
        inside = ntpath.commonpath((candidate, root)) == root and candidate != root
    except ValueError:
        inside = False
    if not inside:
        raise ConfiguratorError(f"{label} is outside the managed pilot path")
    return candidate


def extract_upgrade_preferences(
    manifest: dict[str, Any],
    *,
    agent_root: str,
    config_root: str,
    dedicated_user_data_dir: str,
) -> dict[str, Any]:
    """Extract only supported user choices from a prior pilot manifest.

    Runtime, executable, output, and browser paths are deliberately not copied
    into the new manifest. The installer supplies freshly verified paths; this
    function only proves that the source is a managed Windows pilot and returns
    the small preference set that users can change through the settings tool.
    """
    if not isinstance(manifest, dict):
        raise ConfiguratorError("installed pilot manifest root must be an object")
    expected_identity = {
        "schemaVersion": 1,
        "deploymentId": "corp-browser-agent-windows-pilot",
        "environment": "pilot",
        "target": {"os": "windows", "arch": "x64"},
        "mcpScope": "user",
        "workspaceRoots": [],
    }
    for name, expected in expected_identity.items():
        if manifest.get(name) != expected:
            raise ConfiguratorError(
                f"installed manifest {name} is not a supported Windows user pilot"
            )

    _require_exact_windows_path(
        manifest.get("configRoot"), config_root, "installed configRoot"
    )
    releases_root = ntpath.join(agent_root, "releases")
    install_root = _require_windows_path_within(
        manifest.get("installRoot"), releases_root, "installed runtime"
    )
    runtime_name = ntpath.basename(install_root)
    if not WINDOWS_RUNTIME_DIRECTORY_RE.fullmatch(runtime_name):
        raise ConfiguratorError(
            "installed runtime is not a versioned Windows x64 core release"
        )
    _require_exact_windows_path(
        manifest.get("nodeExecutable"),
        ntpath.join(install_root, "node", "node.exe"),
        "installed nodeExecutable",
    )
    output = manifest.get("output")
    if not isinstance(output, dict):
        raise ConfiguratorError("installed manifest output is missing")
    _require_exact_windows_path(
        output.get("directory"),
        ntpath.join(agent_root, "output", "pilot"),
        "installed output directory",
    )

    browser = manifest.get("browser")
    if not isinstance(browser, dict):
        raise ConfiguratorError("installed manifest browser settings are missing")
    browser_channel = browser.get("channel")
    if browser_channel not in ("chrome", "msedge"):
        raise ConfiguratorError("installed browser channel is unsupported")
    browser_executable = browser.get("executablePath")
    if (
        not isinstance(browser_executable, str)
        or not ntpath.isabs(browser_executable)
        or not browser_executable.lower().endswith(".exe")
    ):
        raise ConfiguratorError("installed browser executable is not a Windows .exe")

    mode = manifest.get("mode")
    if mode == "extension":
        approval = browser.get("manualConnectionApproval")
        if type(approval) is not bool:
            raise ConfiguratorError(
                "installed extension approval setting is not boolean"
            )
        if browser.get("extensionDistribution") != "manual-pilot":
            raise ConfiguratorError(
                "installed extension distribution is not the pilot channel"
            )
        if "userDataDir" in browser or "headless" in browser:
            raise ConfiguratorError(
                "installed extension mode contains dedicated Profile settings"
            )
        browser_mode = "extension"
        headless = False
        extension_authorization = "session" if approval else "user"
    elif mode == "persistent":
        headless_value = browser.get("headless")
        if type(headless_value) is not bool:
            raise ConfiguratorError("installed dedicated headless setting is not boolean")
        _require_exact_windows_path(
            browser.get("userDataDir"),
            dedicated_user_data_dir,
            "installed dedicated Profile",
        )
        if (
            "extensionDistribution" in browser
            or "manualConnectionApproval" in browser
        ):
            raise ConfiguratorError(
                "installed dedicated mode contains extension authorization settings"
            )
        browser_mode = "dedicated"
        headless = headless_value
        extension_authorization = "session"
    else:
        raise ConfiguratorError("installed browser mode is unsupported")

    interaction = manifest.get("interaction")
    legacy_interaction_defaults = interaction is None
    if interaction is None:
        snapshot_strategy = "compact"
        compatibility_mode = "robust"
    elif isinstance(interaction, dict):
        snapshot_strategy = interaction.get("snapshotStrategy")
        compatibility_mode = interaction.get("compatibilityMode")
        if snapshot_strategy not in ("compact", "full"):
            raise ConfiguratorError(
                "installed snapshot strategy is unsupported"
            )
        if compatibility_mode not in ("robust", "standard"):
            raise ConfiguratorError(
                "installed compatibility mode is unsupported"
            )
    else:
        raise ConfiguratorError("installed interaction settings are malformed")

    return {
        "source": "existing",
        "browserMode": browser_mode,
        "browserChannel": browser_channel,
        "headless": headless,
        "extensionAuthorization": extension_authorization,
        "snapshotStrategy": snapshot_strategy,
        "compatibilityMode": compatibility_mode,
        "legacyInteractionDefaultsApplied": legacy_interaction_defaults,
    }


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
        # Write exact UTF-8/LF bytes without relying on Path.write_text's
        # Python-version-specific ``newline`` parameter (absent in Python 3.9).
        temporary.write_bytes(text.encode("utf-8"))
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


def apply_interaction_preferences(
    manifest: dict[str, Any],
    *,
    snapshot_strategy: str,
    compatibility_mode: str,
) -> None:
    if snapshot_strategy not in {"compact", "full"}:
        raise ConfiguratorError("snapshot strategy must be compact or full")
    if compatibility_mode not in {"robust", "standard"}:
        raise ConfiguratorError("compatibility mode must be robust or standard")
    interaction = manifest.get("interaction")
    if not isinstance(interaction, dict):
        raise ConfiguratorError("manifest interaction settings are missing")
    interaction["snapshotStrategy"] = snapshot_strategy
    interaction["compatibilityMode"] = compatibility_mode


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
    apply_interaction_preferences(
        manifest,
        snapshot_strategy=getattr(
            args,
            "snapshot_strategy",
            manifest["interaction"]["snapshotStrategy"],
        ),
        compatibility_mode=getattr(
            args,
            "compatibility_mode",
            manifest["interaction"]["compatibilityMode"],
        ),
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


def inspect_upgrade(args: argparse.Namespace) -> None:
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfiguratorError(
            f"cannot read installed pilot manifest: {exc}"
        ) from exc
    preferences = extract_upgrade_preferences(
        manifest,
        agent_root=args.agent_root,
        config_root=args.config_root,
        dedicated_user_data_dir=args.dedicated_user_data_dir,
    )
    if args.preferences_out.exists() and not args.force:
        raise ConfiguratorError(
            f"refusing to overwrite {args.preferences_out}; pass --force"
        )
    atomic_write_text(
        args.preferences_out,
        json.dumps(preferences, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    print(
        "VALID UPGRADE PREFERENCES: "
        f"mode={preferences['browserMode']}; "
        f"authorization={preferences['extensionAuthorization']}; "
        f"headless={str(preferences['headless']).lower()}; "
        f"snapshot={preferences['snapshotStrategy']}; "
        f"compatibility={preferences['compatibilityMode']}"
    )


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
    apply_interaction_preferences(
        manifest,
        snapshot_strategy=getattr(args, "snapshot_strategy", "compact"),
        compatibility_mode=getattr(args, "compatibility_mode", "robust"),
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
    generate_parser.add_argument(
        "--snapshot-strategy", choices=("compact", "full"), default="compact"
    )
    generate_parser.add_argument(
        "--compatibility-mode", choices=("robust", "standard"), default="robust"
    )
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
    reconfigure_parser.add_argument(
        "--snapshot-strategy", required=True, choices=("compact", "full")
    )
    reconfigure_parser.add_argument(
        "--compatibility-mode", required=True, choices=("robust", "standard")
    )
    reconfigure_parser.add_argument("--force", action="store_true")
    inspect_parser = subparsers.add_parser("inspect-upgrade")
    inspect_parser.add_argument("--manifest", required=True, type=Path)
    inspect_parser.add_argument("--agent-root", required=True)
    inspect_parser.add_argument("--config-root", required=True)
    inspect_parser.add_argument("--dedicated-user-data-dir", required=True)
    inspect_parser.add_argument("--preferences-out", required=True, type=Path)
    inspect_parser.add_argument("--force", action="store_true")
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
        elif args.command == "inspect-upgrade":
            inspect_upgrade(args)
        else:
            assert_user_paths(args)
    except (ConfiguratorError, browser_agent.ManifestError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
