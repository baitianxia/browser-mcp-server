#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble a verified runtime and the browser-mcp-server deployment package."""

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
import zipfile
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


TOOLKIT_VERSION = "1.0.16"
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
    "NOTICE.md",
    "Makefile",
    "config/deployment.local-demo.json",
    "config/deployment.pilot.json.template",
    "config/deployment.production.json.template",
    "config/deployment.windows-pilot.json.template",
    "config/deployment.windows-production.json.template",
    "config/playwright-extension-source.json",
    "config/settings.example.json",
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
    "docs/adr/0008-publisher-gate-and-single-pass-target-install.md",
    "docs/adr/0009-post-install-browser-settings.md",
    "docs/adr/0010-compact-snapshots-and-dynamic-page-compatibility.md",
    "examples/chrome-policy/extension-settings-self-hosted.json.template",
    "examples/chrome-policy/extension-settings-web-store.json",
    "runtime/bin/chrome-devtools-mcp",
    "runtime/bin/chrome-devtools-mcp.cmd",
    "runtime/bin/intranet-browser-agent-mcp.js",
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
    "scripts/BROWSER-AGENT-SETTINGS.cmd",
    "scripts/BROWSER-AGENT-SETTINGS.ps1",
    "scripts/INSTALL-WINDOWS-PILOT.cmd",
    "scripts/INSTALL-WINDOWS-PILOT.ps1",
    "scripts/INSTALL.cmd",
    "scripts/CONFIGURE.cmd",
    "scripts/OPEN-CONFIG.cmd",
    "scripts/UNINSTALL.cmd",
    "scripts/UNINSTALL.ps1",
    "scripts/prepare_runtime_tree.py",
    "scripts/prepare_windows_node_distribution.py",
    "scripts/register_claude_user_mcp.py",
    "scripts/smoke_playwright_mcp.py",
    "scripts/validate_node_distribution.py",
    "scripts/validate_playwright_extension.py",
    "scripts/validate_windows_release_metadata.py",
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
    "tests/test_mcp_compatibility.py",
    "tests/test_node_distribution.py",
    "tests/test_playwright_mcp_smoke.py",
    "tests/test_playwright_extension.py",
    "tests/test_transfer_kit.py",
    "tests/test_windows_pilot_setup.py",
    "tests/test_windows_release_metadata.py",
    "tools/browser_agent.py",
)

# The user-facing Windows ZIP contains only files needed to install, configure,
# verify, and run the product.  Publisher tests, AST gates, build scripts, and
# source fixtures stay in CI artifacts and are deliberately excluded here.
PUBLIC_WINDOWS_SOURCE_FILES = (
    "config/deployment.windows-pilot.json.template",
    "config/playwright-extension-source.json",
    "config/windows-mcp-environment.json",
    "config/windows-node-sources.json",
    "docs/operations.md",
    "docs/windows-quickstart.md",
    "scripts/BROWSER-AGENT-SETTINGS.ps1",
    "scripts/CONFIGURE.cmd",
    "scripts/check_playwright_extension.py",
    "scripts/configure_windows_pilot.py",
    "scripts/register_claude_user_mcp.py",
    "scripts/smoke_playwright_mcp.py",
    "scripts/validate_node_distribution.py",
    "scripts/validate_playwright_extension.py",
    "scripts/validate_windows_release_metadata.py",
    "scripts/verify-bundle.py",
    "scripts/windows-tool-discovery.ps1",
    "templates/CLAUDE.browser.md",
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


def public_windows_source_files(project_root: Path) -> list[Path]:
    result: list[Path] = []
    for item in sorted(
        (Path(value) for value in PUBLIC_WINDOWS_SOURCE_FILES),
        key=lambda path: path.as_posix(),
    ):
        source = project_root / item
        if source.is_symlink() or not source.is_file():
            raise TransferKitError(
                f"required public Windows source file not found or not regular: {source}"
            )
        result.append(item)
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


def validate_public_windows_build_metadata(metadata: dict[str, object]) -> None:
    """Reject cross-built or otherwise unverified runtimes before packaging.

    The public Windows ZIP is an installable user artifact.  A runtime that is
    merely target-labelled as Windows can still have been produced on another
    host or without the target CLI smoke test; allowing it into the ZIP would
    create a package that only fails later in the installer.  Keep this check
    at the builder boundary so the artifact can never be published in that
    state.
    """
    target = metadata.get("target")
    build_host = metadata.get("buildHost")
    if target != {"system": "windows", "machine": "x64"}:
        raise TransferKitError("Windows public packages require target windows/x64")
    if metadata.get("crossBuilt") is not False:
        raise TransferKitError("Windows public packages require crossBuilt=false")
    if metadata.get("targetCliSmokeTested") is not True:
        raise TransferKitError("Windows public packages require targetCliSmokeTested=true")
    if build_host != {"system": "windows", "machine": "x64"}:
        raise TransferKitError("Windows public packages require a native windows/x64 build host")
    if metadata.get("profile") != "core":
        raise TransferKitError("Windows public packages require the core runtime profile")
    if metadata.get("bundledNode") is not True:
        raise TransferKitError("Windows public packages require bundledNode=true")


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
    if target_system == "windows" and not cross_built:
        verification_section = """## 1. 导入后校验

解压前先按 `SHA256SUMS.txt` 校验 ZIP；具体步骤见 `README.md`。哈希不一致时，不要运行任何文件。

完整测试、PowerShell 语法检查、注册故障矩阵和真实隔离探针已经由发布方 Windows 流水线完成。目标机不重复开发测试；安装器仍会校验当前解压目录、内层运行包、固定哈希的官方 Playwright Extension、包内 Node 和最终 MCP stdio 握手。"""
        deployment_section = """## 3. 双击安装

双击本目录的 `INSTALL.cmd` 一次，无需 UAC，也不填写项目目录、网址、防火墙或审批字段。启动器只启动一个安装/升级事务，不运行单元测试或注册器自检。向导验证 Windows 原生发布元数据和所有包内制品，把运行时和配置安装到当前用户 `%USERPROFILE%\browser-mcp-server`，自动识别 Chrome 或 Edge；扩展安装和人工授权仍受浏览器安全边界约束。随后复用当前用户已有的原生 `claude.exe` 或 npm `claude.cmd`，把直接执行包内 `node.exe + 固定兼容层` 的 MCP 注册到 Claude Code user scope，名称为 `browser-mcp`（显示名“浏览器助手”）。npm 入口只解析现有安装并直接运行其 Node/CLI，不执行 npm；找不到可用 Claude 时安全停止，绝不安装、升级或修复 Claude Code。

向导会自动验证运行包、在同盘暂存后发布固定版本、生成清单并先暂存/preflight 再整目录切换配置。随后逐字节备份真实 Claude 用户配置，事务化注册并核对用户级 MCP 命令、参数及固定环境。首次安装明确没有旧 MCP 条目时会直接继续，其他删除错误会恢复并停止。安装后还会以最终命令完成 MCP stdio `initialize + tools/list` 握手。它不会创建项目目录，也不会写项目 `.mcp.json`/`CLAUDE.md`。任何校验失败都会停止并恢复用户配置和旧部署配置；不会运行 `npm install`、`pnpm install` 或 `npx`。

安装成功后可删除迁移包。以后双击 `%USERPROFILE%\\browser-mcp-server\\CONFIGURE.cmd`，或直接编辑 `%USERPROFILE%\\browser-mcp-server\\config\\settings.json`，可以切换浏览器模式、授权、快照和动态页面兼容方式；设置工具只写入该独立配置目录，并保留失败回滚副本。

完整操作说明见 `toolkit/docs/windows-quickstart.md`；只有生产部署、回滚或故障处理才需要阅读 `toolkit/docs/operations.md`。

## 4. 第一次只读测试

在向导显示完成后，重启 Claude Code，在任意项目输入 `/mcp` 确认 `browser-mcp`。首次调用浏览器工具时，在 Playwright Extension 页面选择一个已经登录的现有标签页；第一次只读取页面标题，不提交、不上传、不删除。"""
    elif target_system == "windows":
        verification_section = """## 1. 仅供发布侧结构审查

本包是非 Windows 主机生成的交叉构建候选，没有完成目标 CLI 冒烟和受控 Windows 发布门禁。它不能交给内网用户安装，也不能通过在目标机补跑开发测试变成正式制品。请在 Windows x64 发布流水线重新构建。"""
        deployment_section = """## 3. 不可安装

不要在目标机双击 `INSTALL.cmd`。安装器会在任何持久化变更前拒绝 `crossBuilt=true` 或 `targetCliSmokeTested=false` 的包。只有发布流水线上传的 Windows x64 原生正式包可以进入内网试点。"""
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

将运行包解压到新的版本目录，不覆盖当前版本；按清单安装 `playwright.config.json` 与 `interaction.config.json`，把 `.mcp.json` 放到 Claude Code 项目根，并合并 `CLAUDE.browser.md`。然后执行 `preflight`。

完整路径、权限、人工登录、回滚和验收步骤见 `toolkit/docs/operations.md` 与 `toolkit/docs/acceptance.md`。首次只做只读测试，SSO/MFA 由人完成。"""
    cross_build_note = (
        "\n> **Windows 交叉构建候选：** 仅供发布侧结构审查；目标安装器会拒绝。请从受控 Windows x64 流水线重新构建并验证正式包。\n"
        if cross_built
        else ""
    )
    package_name = f"{kit_name}.zip" if target_system == "windows" else f"{kit_name}.tar.gz"
    return f"""# 浏览器助手迁移包

本包包含固定版本运行时和部署工具链，不包含真实环境清单、登录秘密、企业扫描结果或签名。

- 迁移包：`{package_name}`
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


def write_checksum_manifest(root: Path, filename: str = "SHA256SUMS.txt") -> Path:
    """Write a canonical, POSIX-path SHA-256 manifest for a staged bundle."""
    entries: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.name == filename:
            continue
        relative = path.relative_to(root).as_posix()
        entries.append(f"{sha256(path)}  {relative}")
    target = root / filename
    target.write_text("\n".join(entries) + "\n", encoding="utf-8", newline="")
    return target


def add_zip_tree(bundle: zipfile.ZipFile, root: Path, archive_root: str) -> None:
    """Add a directory tree to a ZIP using stable POSIX member names."""
    directories = [root, *sorted(root.rglob("*"), key=lambda item: item.as_posix())]
    for entry in directories:
        relative = entry.relative_to(root)
        member = PurePosixPath(archive_root, relative.as_posix()).as_posix()
        if entry.is_dir():
            bundle.writestr(member.rstrip("/") + "/", b"")
        elif entry.is_file():
            bundle.write(entry, member)


def public_windows_manifest(
    *,
    kit_name: str,
    runtime_name: str,
    runtime_digest: str,
    metadata: dict[str, object],
    source_count: int,
    browser_extension: dict[str, object] | None,
) -> dict[str, object]:
    target = metadata.get("target") if isinstance(metadata.get("target"), dict) else {}
    manifest: dict[str, object] = {
        "schemaVersion": 1,
        "product": "browser-mcp-server",
        "displayName": "浏览器助手",
        "mcpServerName": "browser-mcp",
        "package": {
            "name": kit_name,
            "format": "zip",
            "singleTopLevelDirectory": True,
            "publicDelivery": "zip-only",
            "entrypoint": "INSTALL.cmd",
            "configureEntrypoint": "CONFIGURE.cmd",
            "openConfigEntrypoint": "OPEN-CONFIG.cmd",
            "uninstallEntrypoint": "UNINSTALL.cmd",
            "checksumManifest": "SHA256SUMS.txt",
        },
        "target": target,
        "version": metadata.get("runtimeVersion", TOOLKIT_VERSION),
        "traceability": {
            "runtimeArchive": runtime_name,
            "runtimeSha256": runtime_digest,
            "sourceFileCount": source_count,
            "sourcePolicy": "reviewed-allowlist",
            "buildMetadata": metadata,
        },
        "paths": {
            "installRoot": "%USERPROFILE%\\browser-mcp-server",
            "settings": "%USERPROFILE%\\browser-mcp-server\\config\\settings.json",
        },
    }
    if browser_extension is not None:
        manifest["browserExtension"] = {
            "extensionId": browser_extension.get("extensionId"),
            "version": browser_extension.get("version"),
            "path": f"payload/browser-extension/{browser_extension.get('filename')}",
            "unpackedPath": "payload/browser-extension/unpacked",
        }
    return manifest


def start_here_html(kit_name: str, runtime_name: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>浏览器助手 · browser-mcp-server</title>
  <style>body {{ font-family: system-ui, sans-serif; max-width: 52rem; margin: 3rem auto; padding: 0 1rem; line-height: 1.6; }} code {{ background: #f1f3f5; padding: .15rem .3rem; border-radius: .25rem; }} .action {{ display: inline-block; padding: .7rem 1rem; background: #1463d6; color: white; border-radius: .4rem; text-decoration: none; }}</style>
</head>
<body>
  <h1>浏览器助手</h1>
  <p>browser-mcp-server Windows x64 离线安装包（{kit_name}）。</p>
  <p>先阅读 <code>README.md</code>，再双击下面的唯一安装/升级入口：</p>
  <p><a class="action" href="INSTALL.cmd">INSTALL.cmd</a></p>
  <p>安装完成后使用 <code>CONFIGURE.cmd</code> 修改设置，或使用 <code>OPEN-CONFIG.cmd</code> 打开 <code>%USERPROFILE%\\browser-mcp-server\\config\\settings.json</code>。</p>
  <p>运行时归档：<code>payload/runtime/{runtime_name}</code>。校验清单：<code>SHA256SUMS.txt</code>。</p>
</body>
</html>
"""


def build_windows_public_zip(
    *,
    project_root: Path,
    runtime_archive: Path,
    output_dir: Path,
    force: bool,
    extension_crx: Path,
    extension_approval: dict[str, object] | None,
    metadata: dict[str, object],
    relative_sources: list[Path],
    browser_extension: dict[str, object],
) -> tuple[Path, Path]:
    runtime_name = runtime_archive.name
    runtime_id = runtime_name[: -len(RUNTIME_SUFFIX)]
    version_match = re.search(r"-(\d+\.\d+\.\d+)-", runtime_id)
    version = version_match.group(1) if version_match else TOOLKIT_VERSION
    kit_name = f"browser-mcp-server-{version}-windows-x64"
    archive = output_dir / f"{kit_name}.zip"
    sidecar = Path(f"{archive}.sha256")
    if not force and (archive.exists() or sidecar.exists()):
        raise TransferKitError(f"refusing to overwrite {archive}; pass --force")

    temporary = Path(tempfile.mkdtemp(prefix=".browser-mcp-server.", dir=output_dir))
    verifier = project_root / "scripts" / "verify-bundle.py"
    try:
        stage = temporary / kit_name
        payload = stage / "payload"
        toolkit_root = payload / "toolkit"
        runtime_root = payload / "runtime"
        browser_extension_root = payload / "browser-extension"
        for directory in (toolkit_root, runtime_root, browser_extension_root):
            directory.mkdir(parents=True, exist_ok=True)

        for relative in relative_sources:
            destination = toolkit_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project_root / relative, destination, follow_symlinks=False)

        # The payload installer is retained as an implementation detail; users
        # only see the canonical top-level INSTALL.cmd entrypoint.
        for launcher in ("INSTALL-WINDOWS-PILOT.cmd", "INSTALL-WINDOWS-PILOT.ps1"):
            shutil.copy2(project_root / "scripts" / launcher, payload / launcher)
        (payload / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(project_root / "scripts" / "UNINSTALL.ps1", payload / "scripts" / "UNINSTALL.ps1")
        shutil.copy2(runtime_archive, runtime_root / runtime_name)
        shutil.copy2(Path(f"{runtime_archive}.sha256"), runtime_root / f"{runtime_name}.sha256")
        shutil.copy2(extension_crx, browser_extension_root / str(browser_extension["filename"]))
        unpacked = browser_extension_root / "unpacked"
        extract_crx_payload(extension_crx, unpacked)
        validate_unpacked_against_crx(extension_crx, unpacked)

        runtime_digest = sha256(runtime_archive)
        kit_metadata: dict[str, object] = {
            "schemaVersion": 1,
            "toolkitVersion": version,
            "product": "browser-mcp-server",
            "displayName": "浏览器助手",
            "mcpServerName": "browser-mcp",
            "sourceFileCount": len(relative_sources),
            "sourcePolicy": "reviewed-allowlist",
            "runtime": {
                "archive": runtime_name,
                "sha256": runtime_digest,
                "sizeBytes": runtime_archive.stat().st_size,
                "buildMetadata": metadata,
            },
            "browserExtension": {
                **browser_extension,
                "approval": "toolkit/config/playwright-extension-source.json",
                "path": f"browser-extension/{browser_extension['filename']}",
                "unpackedPath": "browser-extension/unpacked",
                "installation": "offline-user-policy-with-manual-unpacked-fallback",
            },
        }
        (payload / "KIT-METADATA.json").write_text(
            json.dumps(kit_metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # Canonical user-facing entrypoints and configuration example.
        shutil.copy2(project_root / "README.md", stage / "README.md")
        shutil.copy2(project_root / "NOTICE.md", stage / "NOTICE.md")
        (stage / "config").mkdir(exist_ok=True)
        shutil.copy2(project_root / "config" / "settings.example.json", stage / "config" / "settings.example.json")
        for name in ("INSTALL.cmd", "CONFIGURE.cmd", "OPEN-CONFIG.cmd", "UNINSTALL.cmd"):
            shutil.copy2(project_root / "scripts" / name, stage / name)
        (stage / "START-HERE.html").write_text(
            start_here_html(kit_name, runtime_name), encoding="utf-8"
        )
        release_manifest = public_windows_manifest(
            kit_name=kit_name,
            runtime_name=runtime_name,
            runtime_digest=runtime_digest,
            metadata=metadata,
            source_count=len(relative_sources),
            browser_extension=browser_extension,
        )
        (stage / "release-manifest.json").write_text(
            json.dumps(release_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_checksum_manifest(stage)
        run_checked((sys.executable, str(verifier), str(stage)))
        metadata_verifier = project_root / "scripts" / "validate_windows_release_metadata.py"
        run_checked((sys.executable, str(metadata_verifier), str(payload / "KIT-METADATA.json")))
        run_checked((sys.executable, str(metadata_verifier), str(stage / "release-manifest.json")))

        staged_archive = temporary / f"{kit_name}.zip"
        with zipfile.ZipFile(staged_archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            add_zip_tree(bundle, stage, kit_name)
        run_checked((sys.executable, str(verifier), str(staged_archive)))
        os.replace(staged_archive, archive)
        sidecar.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8", newline="")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return archive, sidecar


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
    if (
        runtime_archive.is_symlink()
        or runtime_sidecar.is_symlink()
        or not runtime_archive.is_file()
        or not runtime_sidecar.is_file()
    ):
        raise TransferKitError("runtime archive and adjacent .sha256 sidecar are required")

    verifier = project_root / "scripts" / "verify-bundle.py"
    run_checked((sys.executable, str(verifier), str(runtime_archive)))
    metadata = runtime_metadata(runtime_archive)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = metadata.get("target")
    target_system = (
        str(target.get("system", "")).lower() if isinstance(target, dict) else ""
    )
    relative_sources = (
        public_windows_source_files(project_root)
        if target_system == "windows"
        else source_files(project_root)
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
        validate_public_windows_build_metadata(metadata)
    elif extension_crx is not None:
        raise TransferKitError("--extension-crx is only valid for Windows transfer kits")

    if target_system == "windows":
        if extension_crx is None or browser_extension is None:
            raise TransferKitError("validated browser extension metadata is missing")
        return build_windows_public_zip(
            project_root=project_root,
            runtime_archive=runtime_archive,
            output_dir=output_dir,
            force=force,
            extension_crx=extension_crx,
            extension_approval=extension_approval,
            metadata=metadata,
            relative_sources=relative_sources,
            browser_extension=browser_extension,
        )

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
