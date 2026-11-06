# 浏览器助手（browser-mcp-server）

`browser-mcp-server` 是一个离线、用户范围的本地 MCP，显示名为“浏览器助手”，在 Claude Code 中以 `browser-mcp` 出现。它通过本机 stdio 连接固定版本的 Playwright MCP，帮助用户操作自己明确授权的 Chrome 或 Edge 标签页。

安装包不启动 Windows 服务、不监听网络端口、不修改项目 `.mcp.json` 或 `CLAUDE.md`，也不会安装、升级或修复 Claude Code。网页仍受浏览器登录、扩展授权、站点权限、验证码和 MFA 约束；删除、上传、提交、权限变更和对外沟通等高风险动作由 Claude Code 的确认流程处理。

## Windows x64 安装

安装包**不含任何 EXE/DLL**（包括 `node.exe`），只携带固定版本 Playwright MCP 的 JavaScript 运行时，运行时使用这台电脑已有的 Node.js。整个过程不需要管理员权限、项目目录或网址白名单，也不运行 `npm`、`pnpm`、`npx` 或在线下载。

### 1. 安装前确认

| 前置条件 | 要求 | 自查方法（PowerShell） |
| --- | --- | --- |
| 操作系统 | Windows x64，Windows PowerShell 5.1 | — |
| Node.js | 已安装的 **Windows x64 Node.js 20.12.0 或更高版本** | `node -p "process.version+'/'+process.arch"`，应类似 `v20.18.3/x64` |
| Python | 3.10 或更高版本 | `python --version` |
| Claude Code | 当前用户已能运行（原生 `claude.exe` 或 npm 安装的 `claude.cmd`） | `claude --version` |
| 浏览器 | Chrome（优先）或 Edge | — |

安装器只复用并校验这些已有软件，不会安装、升级、下载或修复其中任何一个。Node.js 必须是 x64，WindowsApps 别名和低于 20.12.0 的版本会被拒绝。Node.js 20 已于 2026-04-30 停止维护，20.12.0 只是兼容下限；如果需要新装或升级 Node.js，请按组织软件规范选择仍在维护的版本。

### 2. 校验并解压

发布方交付一个 ZIP 和它的相邻校验文件：

```text
browser-mcp-server-<版本>-windows-x64.zip
browser-mcp-server-<版本>-windows-x64.zip.sha256
```

先核对 SHA-256，再把 ZIP 解压到一个新的本机盘符目录：

```powershell
(Get-FileHash .\browser-mcp-server-<版本>-windows-x64.zip -Algorithm SHA256).Hash.ToLower()
Get-Content .\browser-mcp-server-<版本>-windows-x64.zip.sha256
```

两个哈希不一致时不要运行任何文件。逐字节校验脚本见 [`docs/windows-quickstart.md`](docs/windows-quickstart.md)（解压后位于 `payload/toolkit/docs/windows-quickstart.md`）。解压后只有一个顶层目录 `browser-mcp-server-<版本>-windows-x64`，里面是 `README.md`、`START-HERE.html`、`INSTALL.cmd`、`CONFIGURE.cmd`、`OPEN-CONFIG.cmd`、`UNINSTALL.cmd`、`config/settings.example.json`、`payload/`、`release-manifest.json`、`SHA256SUMS.txt` 和 `NOTICE.md`。不要从 ZIP 预览窗口直接运行文件。

### 3. 双击 `INSTALL.cmd`

进入顶层目录，双击一次 `INSTALL.cmd`。首次安装和升级都用它，安装器会依次完成：

1. 校验包清单、发布元数据和内层运行包，确认包内没有 EXE/DLL。
2. 按 PATH 中的 `node.exe`、`%NVM_SYMLINK%\node.exe`、`%ProgramFiles%\nodejs\node.exe` 的顺序查找 Node.js，检查版本（≥ 20.12.0）、架构（x64）和路径。屏幕上会显示选中的版本和路径，例如“将使用这台电脑已有的 Node.js v20.18.3（x64）：C:\nvm4w\nodejs\node.exe”。**找不到可用 Node.js 时，安装器在改动任何文件之前就停止**，并列出检查过的每个候选及拒绝原因。
3. 把运行时和配置安装到 `%USERPROFILE%\browser-mcp-server`，设置文件为 `%USERPROFILE%\browser-mcp-server\config\settings.json`，并自动识别 Chrome 或 Edge。
4. 检查 Playwright Extension；需要安装时按下一节辅助完成。
5. 在 Claude Code user scope 注册 `browser-mcp`（显示名“浏览器助手”）。MCP 命令就是第 2 步选中的 `node.exe` 绝对路径，参数是固定的兼容层入口，不经过 `.cmd`。
6. 用最终命令完成一次 MCP stdio `initialize + tools/list` 握手。

运行时、配置和 Claude Code 条目会在同一事务中暂存、预检、发布；任何一步失败都会恢复旧配置和旧注册，并保留带时间戳的备份。安装器不写项目 `.mcp.json` 或 `CLAUDE.md`，不启动 Windows 服务，也不监听网络端口。

### 4. 浏览器扩展辅助安装

默认的扩展模式通过官方 Playwright Extension 操作你已经登录的标签页（之后可用 `CONFIGURE.cmd` 切换到独立 Profile 或无头模式）。包内带有固定哈希的扩展，当前批准版本为 `0.4.0`：

- 已启用的 `0.4.0` 直接复用；其他版本只有进入发布方兼容批准列表才可选择保留，否则进入离线修复。
- 需要安装或修复时，安装器先尝试当前用户的离线策略。
- 策略被企业 ACL 或浏览器限制时，安装器会打开扩展页，显示扩展目录的完整路径，然后等待你完成：
  1. 打开 `chrome://extensions` 或 `edge://extensions`。
  2. 开启“开发者模式”。
  3. 点击“加载已解压的扩展程序”，选择安装器显示的目录。

保持安装窗口打开。扩展加载后，同一个安装进程会自动继续，不需要再次双击。

### 5. 验证

安装完成后重启 Claude Code，在任意项目输入 `/mcp`，应看到 `browser-mcp`（浏览器助手）。第一次调用浏览器工具时，在 Playwright Extension 页面选择一个已经登录的现有标签页，只读取页面标题，不提交、不上传、不删除。

### 从 1.0.21 及更早版本升级

直接双击新包的 `INSTALL.cmd`，不要先卸载。注意新增了前置条件：1.0.21 及更早版本自带 `node.exe`，从 1.0.22 起改用这台电脑已有的 Node.js 20.12.0+，请先按第 1 步确认。升级后 MCP 命令会切换到系统 `node.exe`；旧版本留在 `%USERPROFILE%\browser-mcp-server` 内的 `node.exe` 不会再被使用。浏览器模式、授权、无头和交互设置会从旧配置迁移。

### 常见问题

- **安装器提示“未找到可用的 Node.js”**：查看提示中列出的候选和原因（版本过低、不是 x64、WindowsApps 别名、文件不存在等），请管理员安装组织批准的 Windows x64 Node.js 20.12.0+，或确认当前用户在新开的 PowerShell 中能运行 `node --version`，然后重新双击 `INSTALL.cmd`。
- **之后卸载、降级或移动了 Node.js**：降级到 20.12.0 以下时，`browser-mcp` 启动会失败并写明所需版本；卸载或移动后，Claude Code 的 `/mcp` 会显示 `browser-mcp` 连接失败。恢复 Node.js，或重新双击 `INSTALL.cmd` 重新选择。使用 nvm 时登记的是 nvm 的链接路径（例如 `C:\nvm4w\nodejs\node.exe`），切换到 20.12.0 以上的版本不需要重装。
- **双击后立即出现“此系统上禁止运行脚本”、`SecurityError` 或 `UnauthorizedAccess`，且没有生成日志**：通常是企业执行策略在 PowerShell 读取脚本前阻止了启动。入口只以进程级 `-ExecutionPolicy Bypass` 启动，不修改系统策略。请运行 `Get-ExecutionPolicy -List` 并把输出交给企业管理员；`MachinePolicy` 或 `UserPolicy` 已锁定时必须按企业签名或策略流程处理，不能通过本工具绕过。
- **日志位置**：安装日志为 `%TEMP%\browser-mcp-server\INSTALL-*.log`，设置日志为同目录下的 `CONFIGURE-*.log`。报告问题时提供日志尾部、Windows/PowerShell/Python/Node.js 版本和复现步骤，并删除令牌、Cookie 和个人路径。

### 卸载

双击解压目录中的 `UNINSTALL.cmd`。它只移除 Claude Code 中的 `browser-mcp` 注册，并把 `%USERPROFILE%\browser-mcp-server` 整体移到 `%USERPROFILE%\browser-mcp-server-backups\uninstall-*` 作为可恢复备份；不会卸载 Node.js、Python 或 Claude Code，也不会改动浏览器。不再需要 Playwright Extension 时，可在扩展页自行移除。

## 设置、状态和重载

双击 `%USERPROFILE%\browser-mcp-server\CONFIGURE.cmd`，或使用包目录中的 `CONFIGURE.cmd`。它会调用已安装的设置工具，不会改写项目文件。也可以双击 `OPEN-CONFIG.cmd` 直接打开 `config/settings.json`。

MCP 本身提供三个配置工具：

- `browser_config_status`：显示产品身份、设置路径、当前模式、缺失字段和下一步命令；令牌等秘密会被遮蔽。
- `browser_configure`：只接受经过校验的非秘密字段，以临时文件和原子替换写入设置文件。
- `browser_config_reload`：重新读取磁盘设置并应用快照/动态页面兼容选项；浏览器启动方式或可执行文件变化时重启 Claude Code MCP 进程。

兼容层还提供 `browser_paste`/`browser_copy`（显式 OS 剪贴板 + 可信快捷键）、`browser_register_helper`/`browser_call_helper`（进程内 TTL helper）、`browser_sheet_bridge`（MODOC `window.sheetInst` 探测及受限的读、定位、写调用）。页面 Clipboard API 仍由 `browser_clipboard` 单独负责，HTTP 页面不会自动读取用户系统剪贴板。涉及输入、剪贴板或页面代码的调用可传 `expectedUrl`，标签页漂移时工具会失败关闭。

需要让一次任务结束后自动收拢新标签页时，先调用 `browser_task_start`，任务完成后调用 `browser_task_cleanup` 并传 `{"confirm":true}`。兼容层会在当前 Playwright 连接中记录实际通过 `BrowserContext.newPage()` 创建的 Page 对象；导航、重载、同名网址和索引变化不会改变归属，只关闭本次任务创建的页面，原有页面保留。`keepTabs:true` 可在不关闭页面的情况下结束记录。这个生命周期需要调用方明确发出开始和结束调用，MCP 不会用空闲时间猜测业务任务已经完成，也不会在进程崩溃或页面上下文重置后猜测要关闭哪些页面。

同一个活动 Playwright 连接中的新页面会继续使用已有的 Playwright 分组。扩展 0.4.0 在重新建立连接时会清理旧分组并建立新连接分组；当前签名扩展不能安全地跨连接复用旧分组，因此不能通过修改运行时绕过这一边界。任务清理后空分组由 Chrome/扩展管理；仍有用户页面的分组不会被自动拆散。

扩展令牌只能暂时通过注册器进程环境传递，并且唯一持久化在 Claude Code 当前用户配置中。它不会写入 ZIP、`settings.json`、部署清单、项目文件、日志或 MCP 响应。

## 开发与验证

本仓库保留离线构建、清单渲染、运行包完整性和 Windows 发布门禁脚本。常用检查：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify-bundle.py <bundle-or-extracted-directory>
```

跨项目 MCP 的 Claude Code 探测、user-scope 注册事务和 stdio 冒烟实现位于独立公共包 [`mcp-engineering-standards`](https://github.com/baitianxia/mcp-engineering-standards)。本仓库的公共规范和邮件/数据库/workspace 工程盘点见 [`docs/mcp-engineering-standard.md`](docs/mcp-engineering-standard.md)；browser 工程只维护自己的 `browser-mcp` server name、配置路径、参数和 required tools。

Windows 正式包必须在原生 Windows x64 构建机生成，并由 `scripts/validate_windows_release_metadata.py`、`scripts/verify-bundle.py` 和发布流水线复验。当前开发主机未执行真实 Windows x64 安装验收时，不能把本地结果描述为 Windows 已验收。
