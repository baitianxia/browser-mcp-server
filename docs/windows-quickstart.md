# Windows 内网试点快速开始

状态：当前规范。本文只适用于发布方已经在原生 Windows x64、Windows PowerShell 5.1 上验证的正式 ZIP。交叉构建候选包不能交给用户安装。

## 目标机要求

目标机需要 Windows x64、Python 3.10 或更高版本、Chrome（优先）或 Edge，以及当前用户已经可以正常运行的 Claude Code。Claude Code 可以是原生 `claude.exe`，也可以是现有 npm 安装提供的 `claude.cmd`；安装器只复用并验证现有入口，不安装、升级、替换或修复 Claude Code。目标机不需要系统 Node.js，正式包会携带批准的 Windows x64 Node.js 和固定 Playwright MCP 运行时。整个目标机流程不运行 `npm`、`pnpm`、`npx` 或在线下载。

## 校验并解压

发布方交付一个文件和它的相邻校验文件：

```text
browser-mcp-server-<版本>-windows-x64.zip
browser-mcp-server-<版本>-windows-x64.zip.sha256
```

在新的本机盘符目录打开 PowerShell，先校验 ZIP，再解压到空目录：

```powershell
$Archive = (Resolve-Path .\browser-mcp-server-<版本>-windows-x64.zip).Path
$Sidecar = "$Archive.sha256"
$Actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
$ExpectedBytes = [Text.Encoding]::UTF8.GetBytes(("{0}  {1}`n" -f $Actual, [IO.Path]::GetFileName($Archive)))
$SidecarBytes = [IO.File]::ReadAllBytes($Sidecar)
if ($ExpectedBytes.Length -ne $SidecarBytes.Length) {
    throw "ZIP SHA-256 校验文件不是规范的小写、双空格、文件名和 LF 格式。"
}
for ($Index = 0; $Index -lt $ExpectedBytes.Length; $Index++) {
    if ($ExpectedBytes[$Index] -ne $SidecarBytes[$Index]) {
        throw "ZIP SHA-256 校验文件不是规范的小写、双空格、文件名和 LF 格式。"
    }
}
Expand-Archive -LiteralPath $Archive -DestinationPath .\unpacked -Force
```

解压后必须只有一个顶层目录，目录名形如 `browser-mcp-server-<版本>-windows-x64`。不要从 ZIP 预览窗口直接运行文件，也不要把 `payload` 或 `toolkit` 当作用户入口。

## 首次安装或升级

进入唯一顶层目录，双击一次：

```text
INSTALL.cmd
```

这是首次安装和升级共用的入口。它会验证包清单、固定运行时、批准的扩展和现有 Claude Code，然后把本工程安装到：

```text
%USERPROFILE%\browser-mcp-server
```

配置文件位于：

```text
%USERPROFILE%\browser-mcp-server\config\settings.json
```

安装器不请求 UAC，不询问项目目录、网址白名单、防火墙或审批单号，也不写项目 `.mcp.json` 或 `CLAUDE.md`。失败时会保留旧版本和配置，并把本次变更隔离到备份目录；不要先卸载再升级。

如果浏览器扩展尚未安装，安装器会先尝试当前用户的离线策略。策略受企业 ACL 或浏览器限制时，它会打开扩展页并显示包内 `payload/browser-extension/unpacked` 的完整路径，等待用户完成：

1. 打开 `chrome://extensions` 或 `edge://extensions`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”，选择安装器显示的目录。

保持安装窗口打开。扩展加载后，同一个安装进程会继续完成配置、注册和 MCP 握手，不需要再次双击。

## 第一次使用

安装完成后重启 Claude Code，在任意项目输入 `/mcp`，确认看到 `browser-mcp`（显示名“浏览器助手”）。第一次调用只读取一个已授权标签页的标题；连接 Playwright Extension 时选择已经登录的现有标签页。删除、上传、提交、权限变更和对外沟通仍须经过确认。

## 配置、状态和重载

安装后可以双击：

```text
%USERPROFILE%\browser-mcp-server\CONFIGURE.cmd
```

也可以双击 `OPEN-CONFIG.cmd` 打开设置文件。MCP 提供三个工具：

- `browser_config_status` 显示绝对配置路径、模式、缺失字段和下一步命令；秘密只显示遮罩值。
- `browser_configure` 校验并原子写入允许的非秘密设置。
- `browser_config_reload` 重新读取磁盘设置；改变浏览器启动方式或可执行文件时，重启 Claude Code MCP 进程。

设置文件独立于运行时版本和 Claude 配置。扩展令牌只在注册事务中通过临时进程环境传递，唯一持久化在 Claude Code 当前用户配置中，不进入 ZIP、设置文件、部署清单、日志或 MCP 响应。

## 日志和故障报告

安装日志位于：

```text
%TEMP%\browser-mcp-server\INSTALL-*.log
```

设置日志位于同一目录下的 `CONFIGURE-*.log`。报告问题时提供日志尾部、Windows/PowerShell/Python 版本和复现步骤，删除令牌、Cookie 和个人路径。不要运行 npm、pnpm、npx 或“修复安装”命令。

企业签名、SCA/恶意代码扫描、组策略、SSO/MFA 和真实业务页面仍须由发布与企业验收流程单独确认；本机开发主机的测试结果不能代替原生 Windows 验收。
