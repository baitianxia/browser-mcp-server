# Windows 内网试点：解压后只双击一次

状态：当前；只适用于发布流水线已经验证并上传的 Windows x64 正式试点包。交叉构建候选不能安装到目标机。生产部署仍以 `operations.md` 为准。

目标机只需准备：Windows x64、当前用户已经能正常运行的 Claude Code、Chrome（优先）或 Edge、Python 3.10+。Claude Code 可以来自原生安装的 `claude.exe`，也可以来自 npm 全局安装生成的 `claude.cmd`。向导只复用现有安装，绝不安装、升级、替换或修复 Claude Code。目标机可以完全离线；迁移包同时携带固定 Node.js、固定哈希的官方 Playwright Extension CRX 和经逐文件核对的已解压副本。系统已有的 v20.18.3 可以保留；包内 Playwright MCP 使用自己的固定 Node，npm 版 Claude 继续使用它现有安装本来使用的 Node。整个目标机流程不会运行 npm、pnpm 或 npx。原生 Claude 已安装到 `%USERPROFILE%\.local\bin\claude.exe` 时，即使 Explorer 或当前 PowerShell 的 `PATH` 尚未刷新也能识别；npm 版要求当前用户环境能从 `PATH` 找到 `claude.cmd` 及其现有 `node.exe`。迁移包必须放在本机盘符目录，不使用 UNC。

若目标浏览器尚未安装 Playwright Extension，向导先尝试写入当前用户的 Chrome/Edge `ExtensionInstallForcelist`，从包内 `file:///` CRX 全自动离线安装，不访问 Chrome Web Store。Chrome 对本地 CRX 的静默安装可能要求设备受企业管理；注册表策略键被企业 ACL 拒绝、不可读/不可写、更新清单不可写或无法自动启动浏览器时，向导会把这项自动化记为不可用并立即转入手动加载，不会停止安装。若临时策略已经写入但浏览器没有实际安装，向导会先恢复原策略。随后它会尽力打开 `chrome://extensions`/`edge://extensions`、把包内已解压目录复制到剪贴板并显示三步指引；页面或剪贴板自动化失败时，屏幕仍会显示扩展页地址和完整目录。保持安装窗口打开，按提示开启开发者模式、点击“加载已解压的扩展程序”并选择该目录；安装器会持续检测，成功后在同一进程自动继续，不需要再运行一次。

## 文件准备：校验并解压

把迁移包和相邻 `.sha256` 放进一个新的空目录，在该目录打开 PowerShell：

```powershell
$Archive = Resolve-Path .\intranet-browser-agent-transfer-1.0.14-core-windows-x64.tar.gz
$Expected = (((Get-Content "$($Archive.Path).sha256" -Raw) -split '\s+')[0]).ToLowerInvariant()
$Actual = (Get-FileHash -LiteralPath $Archive.Path -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Actual -ne $Expected) { throw "迁移包 SHA-256 不匹配" }
tar.exe -xzf $Archive.Path
```

哈希不一致就停止，不要运行解压出的任何文件。

## 安装：只双击一次

进入解压出的 `intranet-browser-agent-transfer-1.0.14-core-windows-x64` 目录，只双击下面这一个文件：

```text
INSTALL-WINDOWS-PILOT.cmd
```

不需要先运行门禁脚本，也不需要安装结束后再运行第二个脚本。发布方已经在 Windows 流水线完成完整测试、PowerShell 语法检查、注册故障演练和真实兼容性门禁；目标机不会重复这些开发测试。这个启动器只启动一次安装器，在一个进程中完成目标机所需校验、安装、注册和失败回滚。

无需 UAC，也不需要填写项目目录、网址、网络强制层或审批字段。向导把运行时、配置、输出和离线扩展文件安装到当前用户的 `%LOCALAPPDATA%\IntranetBrowserAgent`，自动识别 Chrome（优先）或 Edge（兜底），缺少扩展时先自动离线安装；仅当浏览器拒绝时才需要按屏幕提示加载一次包内目录，原安装进程会等待并续跑。随后向导将 `intranet-browser-agent` 注册为 Claude Code user-scope MCP。初始设置不创建专用 Profile，而是通过扩展连接用户明确选择的现有标签页，因此可以复用目标机 Chrome 已有 Cookie、SSO 登录态、客户端证书和已安装浏览器扩展。它不会询问或创建项目目录，不会写任何项目文件。

试点不配置网址白名单，Playwright MCP 按默认行为允许访问这台机器当前网络能够访问的全部网址。向导不会设置或询问防火墙，也不要求负责人、变更单、数据分类、模型路由或审批单号。

向导会自动完成以下工作：

1. 检查 Windows x64、Python 和当前用户已有的 Claude Code，校验迁移包来自 Windows x64 原生发布流水线；交叉构建或未完成目标 CLI 验证的包会在写入前拒绝。
2. 校验迁移目录、内层运行包以及官方 Playwright Extension CRX 的固定 ID、版本、哈希和已解压副本；已安装扩展则直接复用。未安装时先尝试本机离线策略；策略访问被拒绝等准备失败会直接降级，已写策略未生效则先恢复，再显示扩展页地址和唯一目录并等待人工加载，成功后在同一进程继续。自动打开页面或复制路径失败不影响等待。
3. 在 `%LOCALAPPDATA%\IntranetBrowserAgent\staging\r-*` 的短路径同盘目录解压固定运行时，校验包内 Windows x64 Node.js 的批准来源、逐文件哈希、PE 架构和实际版本，通过后才原子发布。运行时、扩展和配置的整目录切换使用同一套原子移动与自动退避重试；安全软件短暂占用时原进程等待释放后继续，无需重新双击。若发现上次遗留的扩展目录不完整，向导会先把它保留到唯一备份目录，再自动重建。
4. 自动生成初始 `extension + 有头 + 每次批准` 部署清单和 Playwright 配置，绑定自动识别出的 `chrome`/`msedge` 及实际浏览器 `.exe`，在 `%LOCALAPPDATA%` 内暂存、preflight 后整目录切换。同时安装后续设置入口；浏览器初始使用自己的 last-used Profile，无需填写 Profile 或项目目录。后续失败时本次配置会被隔离到备份目录，再恢复旧配置。
5. 备份真实 Claude Code 用户配置，通过 `--scope user` 事务化执行 `remove → add → get`；注册项直接执行包内 `node.exe + 固定 cli.js + --browser=<channel> + --executable-path=<browser.exe> + config`，并核对实际命令、参数和固定环境。失败时逐字节恢复用户配置、旧部署配置和本次新增的浏览器策略。
6. 对最终路径再次执行 preflight，并用与最终注册一致的直接命令完成 MCP stdio `initialize` 和 `tools/list` 握手；出现任何 `FAIL` 就停止。全程不修改项目 `.mcp.json` 或 `CLAUDE.md`。

首次安装时 Claude 明确报告没有可删除的旧 user-scope MCP 条目是正常情况，向导会记录提示后继续注册；其他删除错误会自动恢复并停止，不需要手工执行任何 Claude/npm 命令。若没有找到可用的现有 Claude Code，向导会在任何真实配置变更前明确停止，不会自行安装。若你本来就设置了 `CLAUDE_CONFIG_DIR`，向导会自动使用它；为避免 Claude 把配置写进当前项目，它必须是本机盘符绝对路径，不能写相对路径、`~` 或 UNC。

成功后，重启 Claude Code，在任意项目中输入 `/mcp`，确认 `intranet-browser-agent` 已连接。第一次调用浏览器工具时，Playwright Extension 会显示连接页；选择一个已经登录的现有 Chrome 标签页或标签组。随后先只测试“读取当前页面标题”，不要提交、上传或删除。连接批准和标签页选择是访问边界，不是第二次安装。

## 安装后更改浏览器设置

以后直接双击下面的文件，不需要迁移包，也不需要重新安装：

```text
%LOCALAPPDATA%\IntranetBrowserAgent\BROWSER-AGENT-SETTINGS.cmd
```

可以选择：

1. 继续使用现有 Chrome/Edge 登录态，并记住当前 Windows 用户。首次需要从自动打开的 Playwright Extension 页面复制一次令牌；以后不再逐次批准。
2. 继续使用现有登录态，但恢复每次连接批准。
3. 使用独立 Profile，不共享原 Chrome/Edge 登录态。可选有头或无头；首次登录、MFA 或验证码必须先用有头模式，登录完成后再切无头。

Extension 连接的是正在显示的浏览器，因此不能使用无头。设置工具会先验证新配置和真实 MCP 握手，成功后才切换；失败会恢复原配置。保存后重启 Claude Code。

向导不会运行 `npm install`、`pnpm install`、`npx`，不会修改系统 Node.js，也不会临时绕过 PowerShell 执行策略。若脚本被企业策略拦截，应走组织签名或脚本批准流程。

若安装失败，窗口会直接显示日志尾部并给出 `%TEMP%\IntranetBrowserAgent\INSTALL-WINDOWS-PILOT-*.log`。设置失败日志为 `%TEMP%\IntranetBrowserAgent\BROWSER-AGENT-SETTINGS-*.log`。日志会自动保留完整输出，直接提供该文件即可定位，不需要为了收集信息重跑。
