#!/usr/bin/env python3
"""Assemble a verified runtime and the deployment toolkit for intranet transfer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterable

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from validate_playwright_extension import (
    ExtensionValidationError,
    extract_crx_payload,
    load_approval as load_extension_approval,
    validate_crx,
    validate_unpacked_against_crx,
)


TOOLKIT_VERSION = "1.0.10"
RUNTIME_PREFIX = "browser-agent-runtime-"
RUNTIME_SUFFIX = ".tar.gz"
RUNTIME_NAME_PATTERN = re.compile(
    r"^browser-agent-runtime-(?P<version>\d+\.\d+\.\d+)-"
    r"(?P<profile>core|diagnostic)-(?P<system>darwin|linux|windows)-"
    r"(?P<machine>arm64|x64)\.tar\.gz$"
)

# Deliberately omit live deployment manifests, VCS data, build output, caches, and
# arbitrary untracked files. New source categories must be reviewed before they
# become part of an intranet transfer artifact.
EXACT_SOURCE_FILES = (
    "README.md",
    "Makefile",
    "config/deployment.local-demo.json",
    "config/deployment.pilot.json.template",
    "config/deployment.production.json.template",
    "config/deployment.windows-pilot.json.template",
    "config/deployment.windows-production.json.template",
    "config/playwright-extension-source.json",
    "config/windows-mcp-environment.json",
    "config/windows-node-sources.json",
    "docs/acceptance.md",
    "docs/architecture.md",
    "docs/design-review.md",
    "docs/operations.md",
    "docs/threat-model.md",
    "docs/verification.md",
    "docs/windows-quickstart.md",
    "docs/adr/0001-local-stdio-and-dedicated-profile.md",
    "docs/adr/0002-hard-boundaries-outside-mcp.md",
    "docs/adr/0003-windows-link-free-runtime.md",
    "docs/adr/0004-minimal-bundled-windows-node.md",
    "docs/adr/0005-windows-pilot-user-scope.md",
    "docs/adr/0006-transactional-mcp-registration-and-windows-gate.md",
    "docs/adr/0007-direct-windows-mcp-executable-and-release-hardening.md",
    "examples/chrome-policy/extension-settings-self-hosted.json.template",
    "examples/chrome-policy/extension-settings-web-store.json",
    "runtime/bin/chrome-devtools-mcp",
    "runtime/bin/chrome-devtools-mcp.cmd",
    "runtime/bin/playwright-mcp",
    "runtime/bin/playwright-mcp.cmd",
    "runtime/package.json",
    "runtime/pnpm-lock.yaml",
    "runtime/pnpm-workspace.yaml",
    "schema/deployment.schema.json",
    "scripts/build-offline-bundle.sh",
    "scripts/build-offline-bundle.ps1",
    "scripts/build-transfer-kit.py",
    "scripts/check_runtime_portability.py",
    "scripts/check_playwright_extension.py",
    "scripts/configure_windows_pilot.py",
    "scripts/create_bundle_archive.py",
    "scripts/generate_sbom.py",
    "scripts/INSTALL-WINDOWS-PILOT.cmd",
    "scripts/INSTALL-WINDOWS-PILOT.ps1",
    "scripts/prepare_runtime_tree.py",
    "scripts/prepare_windows_node_distribution.py",
    "scripts/register_claude_user_mcp.py",
    "scripts/smoke_playwright_mcp.py",
    "scripts/validate_node_distribution.py",
    "scripts/validate_playwright_extension.py",
    "scripts/verify-bundle.py",
    "scripts/verify-windows-release.ps1",
    "scripts/windows-tool-discovery.ps1",
    "scripts/write_archive_hash.py",
    "scripts/write_build_metadata.py",
    "scripts/write_integrity.py",
    "templates/CLAUDE.browser.md",
    "tests/test_artifact_integrity.py",
    "tests/test_browser_agent.py",
    "tests/test_claude_mcp_registration.py",
    "tests/test_node_distribution.py",
    "tests/test_playwright_mcp_smoke.py",
    "tests/test_playwright_extension.py",
    "tests/test_transfer_kit.py",
    "tests/test_windows_pilot_setup.py",
    "tools/browser_agent.py",
)


class TransferKitError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(project_root: Path) -> list[Path]:
    result: list[Path] = []
    for relative in sorted(
        (Path(item) for item in EXACT_SOURCE_FILES), key=lambda item: item.as_posix()
    ):
        source = project_root / relative
        if source.is_symlink() or not source.is_file():
            raise TransferKitError(f"required source file not found or not regular: {source}")
        result.append(relative)
    return result


def run_checked(command: Iterable[str]) -> None:
    try:
        subprocess.run(list(command), check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise TransferKitError(str(exc)) from exc


def runtime_metadata(archive: Path) -> dict[str, object]:
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            matches = [
                member
                for member in bundle.getmembers()
                if PurePosixPath(member.name).name == "BUILD-METADATA.json"
            ]
            if len(matches) != 1 or not matches[0].isfile():
                raise TransferKitError(
                    "runtime archive must contain exactly one regular BUILD-METADATA.json"
                )
            handle = bundle.extractfile(matches[0])
            if handle is None:
                raise TransferKitError("cannot read runtime BUILD-METADATA.json")
            payload = json.loads(handle.read().decode("utf-8"))
    except (OSError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransferKitError(f"cannot read runtime metadata: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise TransferKitError("invalid runtime BUILD-METADATA.json")
    name_match = RUNTIME_NAME_PATTERN.fullmatch(archive.name)
    if name_match is None:
        raise TransferKitError("runtime archive name does not follow the release contract")
    expected_target = {
        "system": name_match.group("system"),
        "machine": name_match.group("machine"),
    }
    if payload.get("runtimeVersion") != name_match.group("version"):
        raise TransferKitError("runtime archive version does not match build metadata")
    if name_match.group("version") != TOOLKIT_VERSION:
        raise TransferKitError(
            f"runtime version must match toolkit version {TOOLKIT_VERSION}"
        )
    if payload.get("profile") != name_match.group("profile"):
        raise TransferKitError("runtime archive profile does not match build metadata")
    if payload.get("target") != expected_target:
        raise TransferKitError("runtime archive target does not match build metadata")
    if expected_target["system"] == "windows" and payload.get("bundledNode") is not True:
        raise TransferKitError("Windows one-click transfer kits require bundledNode=true")
    return payload


def start_here(kit_name: str, runtime_name: str, metadata: dict[str, object]) -> str:
    target = metadata.get("target")
    if isinstance(target, dict):
        target_system = str(target.get("system", "unknown")).lower()
        target_label = f"{target_system} / {target.get('machine', 'unknown')}"
    else:
        target_system = "unknown"
        target_label = "unknown"
    bundled_node = metadata.get("bundledNode") is True
    cross_built = metadata.get("crossBuilt") is True
    node_note = (
        "运行包已携带经过来源与哈希校验的最小 Node.js；无需安装或升级系统 Node.js。"
        if bundled_node
        else "运行包未携带 Node.js；目标机必须提供组织批准的 Node.js 20.19+，并可通过 BROWSER_AGENT_NODE 指定。"
    )
    if target_system == "windows":
        verification_section = """## 1. 导入后校验

解压前先按 `.sha256` 校验迁移 archive；具体复制粘贴命令见 `toolkit/docs/windows-quickstart.md`。哈希不一致时，不要解压或运行任何文件。

双击安装向导后，它会先在同一个窗口自动运行 Windows 发布门禁，在测试前后各校验一次解压目录，校验包内固定哈希的官方 Playwright Extension，临时解压并校验包内运行 archive，再用包内 Node 完成真实 MCP stdio 握手；全部通过才开始安装，任一失败都会在安装前停止。"""
        deployment_section = """## 3. 双击安装

双击本目录的 `INSTALL-WINDOWS-PILOT.cmd` 一次，无需 UAC，也不填写项目目录、网址或审批字段。启动器先自动运行测试前后双重完整性校验、完整测试、全部 PowerShell 脚本语法检查、假 Claude CLI 回滚测试、包内 Node 与官方 Playwright Extension 来源/版本/哈希校验、真实 MCP stdio `initialize + tools/list` 握手和真实 Claude CLI 隔离探针；全部通过后在同一个窗口直接继续安装。向导把运行时安装到当前用户 `%LOCALAPPDATA%`，自动识别 Chrome 或 Edge；若浏览器尚无扩展，先用包内 CRX 和当前用户浏览器策略尝试全自动离线安装。浏览器拒绝该策略时，向导会自动打开扩展页、把包内已解压扩展目录复制到剪贴板并显示三步操作；原安装进程持续等待，检测到加载成功后自动继续，无需重跑。随后用原生 `claude.exe` 把直接执行包内 `node.exe + 固定 cli.js` 的 MCP 注册到 Claude Code user scope；当前用户未被同名高优先级配置覆盖的项目都能使用。试点不配置网址白名单，Playwright MCP 可以操作人通过扩展批准的现有浏览器标签页，并复用其中的登录态。

向导会自动验证运行包、在同盘暂存后发布固定版本、生成清单并先暂存/preflight 再整目录切换配置。注册真实用户配置前，它先用假 Claude CLI 演练首次安装、升级、条目/环境错写和 `remove/add/get` 失败回滚；随后事务化备份、注册并直接核对用户级 MCP 命令、参数及固定环境。固定环境会阻止调用者遗留变量覆盖包内配置，不需要填写。首次安装明确没有旧 MCP 条目时会直接继续，其他删除错误会恢复并停止。它不会创建项目目录，也不会写项目 `.mcp.json`/`CLAUDE.md`。任何校验失败都会停止并恢复用户配置和旧部署配置；不会运行 `npm install`、`pnpm install` 或 `npx`。

完整操作说明见 `toolkit/docs/windows-quickstart.md`；只有生产部署、回滚或故障处理才需要阅读 `toolkit/docs/operations.md`。

## 4. 第一次只读测试

在向导显示完成后，重启 Claude Code，在任意项目输入 `/mcp` 确认 `intranet-browser-agent`。首次调用浏览器工具时，在 Playwright Extension 页面选择一个已经登录的现有标签页；第一次只读取页面标题，不提交、不上传、不删除。"""
    else:
        verification_section = f"""## 1. 导入后立即校验

先验证企业签名和迁移包外层 `.sha256`。解压后在本目录执行：

```bash
python3 toolkit/scripts/verify-bundle.py .
python3 toolkit/scripts/verify-bundle.py runtime/{runtime_name}
```

任一结果不是 `VALID` 都应停止，不要继续解压或部署运行包。"""
        deployment_section = """## 3. 准备试点清单

把 `toolkit/config/deployment.pilot.json.template` 复制到迁移包目录之外，替换全部 `REPLACE-ME`，并填写目标机实际绝对路径、精确 origin、网络强制层和模型路由批准。模板默认未批准，不能直接渲染。

```bash
python3 toolkit/tools/browser_agent.py validate --manifest /path/to/deployment.pilot.json
python3 toolkit/tools/browser_agent.py render --manifest /path/to/deployment.pilot.json --out /path/to/rendered-pilot
```

## 4. 部署与预检

将运行包解压到新的版本目录，不覆盖当前版本；按清单安装 `playwright.config.json`，把 `.mcp.json` 放到 Claude Code 项目根，并合并 `CLAUDE.browser.md`。然后执行 `preflight`。

完整路径、权限、人工登录、回滚和验收步骤见 `toolkit/docs/operations.md` 与 `toolkit/docs/acceptance.md`。首次只做只读测试，SSO/MFA 由人完成。"""
    cross_build_note = (
        "\n> **Windows 自检候选包：** 本运行包在非 Windows 构建主机交叉组装，不能冒充已经过 Windows 验证的正式制品。顶层 `INSTALL-WINDOWS-PILOT.cmd` 会在一次双击流程中强制运行同一 Windows 门禁；门禁只使用临时 Claude 配置，全部通过后才开始持久化安装。完成受控 Windows 与目标业务验收前不得作为生产制品。\n"
        if cross_built
        else ""
    )
    return f"""# 内网测试迁移包

本包包含固定版本运行时和部署工具链，不包含真实环境清单、登录秘密、企业扫描结果或签名。

- 迁移包：`{kit_name}.tar.gz`
- 运行包：`runtime/{runtime_name}`
- 构建目标：`{target_label}`
- Node.js：{node_note}
{cross_build_note}

{verification_section}

## 2. 确认目标兼容

目标 OS/架构必须与上面的构建目标一致；不同平台应在对应受控构建区重新产出。Windows 目标确认 Claude Code、Chrome/Edge 和 Python 3.10+ 可用。若上方显示运行包已携带 Node.js，安装器会使用包内版本且不会修改系统 Node.js；否则目标机才需满足 Node.js 20.19+。

{deployment_section}
"""


def add_tree(bundle: tarfile.TarFile, root: Path, archive_root: str) -> None:
    entries = [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]
    for entry in entries:
        relative = entry.relative_to(root)
        archive_name = Path(archive_root, relative).as_posix()
        bundle.add(entry, arcname=archive_name, recursive=False)


def build_transfer_kit(
    project_root: Path,
    runtime_archive: Path,
    output_dir: Path,
    force: bool,
    extension_crx: Path | None = None,
    *,
    extension_approval: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    project_root = project_root.resolve()
    runtime_archive = runtime_archive.resolve()
    output_dir = output_dir.resolve()
    runtime_name = runtime_archive.name
    if not runtime_name.startswith(RUNTIME_PREFIX) or not runtime_name.endswith(RUNTIME_SUFFIX):
        raise TransferKitError(
            f"runtime archive name must match {RUNTIME_PREFIX}*{RUNTIME_SUFFIX}"
        )
    runtime_sidecar = Path(f"{runtime_archive}.sha256")
    if not runtime_archive.is_file() or not runtime_sidecar.is_file():
        raise TransferKitError("runtime archive and adjacent .sha256 sidecar are required")

    verifier = project_root / "scripts" / "verify-bundle.py"
    run_checked((sys.executable, str(verifier), str(runtime_archive)))
    metadata = runtime_metadata(runtime_archive)
    relative_sources = source_files(project_root)
    target = metadata.get("target")
    target_system = (
        str(target.get("system", "")).lower() if isinstance(target, dict) else ""
    )
    browser_extension: dict[str, object] | None = None
    if target_system == "windows":
        if extension_crx is None:
            raise TransferKitError(
                "Windows transfer kits require --extension-crx for offline Chrome setup"
            )
        extension_crx = extension_crx.resolve()
        try:
            approved = (
                load_extension_approval()
                if extension_approval is None
                else extension_approval
            )
            browser_extension = validate_crx(extension_crx, approved)
        except ExtensionValidationError as exc:
            raise TransferKitError(f"invalid Playwright Extension CRX: {exc}") from exc
    elif extension_crx is not None:
        raise TransferKitError("--extension-crx is only valid for Windows transfer kits")

    runtime_id = runtime_name[: -len(RUNTIME_SUFFIX)]
    kit_name = runtime_id.replace(RUNTIME_PREFIX, "intranet-browser-agent-transfer-", 1)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{kit_name}{RUNTIME_SUFFIX}"
    sidecar = Path(f"{archive}.sha256")
    if not force and (archive.exists() or sidecar.exists()):
        raise TransferKitError(f"refusing to overwrite {archive}; pass --force")

    temporary = Path(tempfile.mkdtemp(prefix=".browser-agent-transfer.", dir=output_dir))
    try:
        stage = temporary / kit_name
        toolkit_root = stage / "toolkit"
        runtime_root = stage / "runtime"
        toolkit_root.mkdir(parents=True)
        runtime_root.mkdir(parents=True)

        for relative in relative_sources:
            destination = toolkit_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project_root / relative, destination, follow_symlinks=False)

        if target_system == "windows":
            for launcher in ("INSTALL-WINDOWS-PILOT.cmd", "INSTALL-WINDOWS-PILOT.ps1"):
                shutil.copy2(project_root / "scripts" / launcher, stage / launcher)
            if browser_extension is None or extension_crx is None:
                raise TransferKitError("validated browser extension metadata is missing")
            browser_extension_root = stage / "browser-extension"
            browser_extension_root.mkdir()
            shutil.copy2(
                extension_crx,
                browser_extension_root / str(browser_extension["filename"]),
                follow_symlinks=False,
            )
            unpacked_extension_root = browser_extension_root / "unpacked"
            extract_crx_payload(extension_crx, unpacked_extension_root)
            validate_unpacked_against_crx(
                extension_crx,
                unpacked_extension_root,
            )

        shutil.copy2(runtime_archive, runtime_root / runtime_name)
        shutil.copy2(runtime_sidecar, runtime_root / runtime_sidecar.name)
        runtime_digest = sha256(runtime_archive)
        kit_metadata = {
            "schemaVersion": 1,
            "toolkitVersion": TOOLKIT_VERSION,
            "sourceFileCount": len(relative_sources),
            "sourcePolicy": "reviewed-allowlist",
            "runtime": {
                "archive": runtime_name,
                "sha256": runtime_digest,
                "sizeBytes": runtime_archive.stat().st_size,
                "buildMetadata": metadata,
            },
        }
        if browser_extension is not None:
            kit_metadata["browserExtension"] = {
                **browser_extension,
                "approval": "toolkit/config/playwright-extension-source.json",
                "path": f"browser-extension/{browser_extension['filename']}",
                "unpackedPath": "browser-extension/unpacked",
                "installation": "offline-user-policy-with-manual-unpacked-fallback",
            }
        (stage / "KIT-METADATA.json").write_text(
            json.dumps(kit_metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (stage / "START-HERE.md").write_text(
            start_here(kit_name, runtime_name, metadata), encoding="utf-8"
        )

        run_checked(
            (
                sys.executable,
                str(project_root / "scripts" / "write_integrity.py"),
                "--root",
                str(stage),
            )
        )
        staged_archive = temporary / f"{kit_name}{RUNTIME_SUFFIX}"
        with tarfile.open(staged_archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
            add_tree(bundle, stage, kit_name)
        run_checked(
            (
                sys.executable,
                str(project_root / "scripts" / "write_archive_hash.py"),
                str(staged_archive),
            )
        )
        run_checked((sys.executable, str(verifier), str(staged_archive)))

        os.replace(staged_archive, archive)
        os.replace(Path(f"{staged_archive}.sha256"), sidecar)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)

    return archive, sidecar


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-archive", required=True, type=Path)
    parser.add_argument(
        "--extension-crx",
        type=Path,
        help="approved Playwright Extension CRX; required for Windows targets",
    )
    parser.add_argument("--output-dir", default=Path("dist"), type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    try:
        archive, sidecar = build_transfer_kit(
            project_root,
            args.runtime_archive,
            args.output_dir,
            args.force,
            args.extension_crx,
        )
    except TransferKitError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Built {archive}")
    print(f"Checksum {sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
