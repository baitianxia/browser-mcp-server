# Windows 内网试点：解压后只双击一次

状态：当前；适用于已通过发布门禁的 Windows x64 试点包，以及明确标记的自检候选包。自检候选不能冒充正式制品，但顶层启动器会在任何持久化安装之前强制运行同一门禁。生产部署仍以 `operations.md` 为准。

目标机只需准备：Windows x64、当前用户已经能正常运行的 Claude Code、Chrome（优先）或 Edge、Python 3.10+。Claude Code 可以来自原生安装的 `claude.exe`，也可以来自 npm 全局安装生成的 `claude.cmd`。向导只复用现有安装，绝不安装、升级、替换或修复 Claude Code。目标机可以完全离线；迁移包同时携带固定 Node.js、固定哈希的官方 Playwright Extension CRX 和经逐文件核对的已解压副本。系统已有的 v20.18.3 可以保留；包内 Playwright MCP 使用自己的固定 Node，npm 版 Claude 继续使用它现有安装本来使用的 Node。整个目标机流程不会运行 npm、pnpm 或 npx。原生 Claude 已安装到 `%USERPROFILE%\.local\bin\claude.exe` 时，即使 Explorer 或当前 PowerShell 的 `PATH` 尚未刷新也能识别；npm 版要求当前用户环境能从 `PATH` 找到 `claude.cmd` 及其现有 `node.exe`。迁移包必须放在本机盘符目录，不使用 UNC。

若目标浏览器尚未安装 Playwright Extension，向导先写入当前用户的 Chrome/Edge `ExtensionInstallForcelist`，尝试从包内 `file:///` CRX 全自动离线安装，不访问 Chrome Web Store。Chrome 对本地 CRX 的静默安装可能要求设备受企业管理；若浏览器没有实际安装，向导会恢复临时策略、自动打开 `chrome://extensions`/`edge://extensions`、把包内已解压目录复制到剪贴板并显示三步指引。此时保持安装窗口打开，按提示开启开发者模式、点击“加载已解压的扩展程序”并选择该目录；安装器会持续检测，成功后在同一进程自动继续，不需要再运行一次。

## 文件准备：校验并解压

把迁移包和相邻 `.sha256` 放进一个新的空目录，在该目录打开 PowerShell：

```powershell
$Archive = Resolve-Path .\intranet-browser-agent-transfer-1.0.10-core-windows-x64.tar.gz
$Expected = (((Get-Content "$($Archive.Path).sha256" -Raw) -split '\s+')[0]).ToLowerInvariant()
$Actual = (Get-FileHash -LiteralPath $Archive.Path -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Actual -ne $Expected) { throw "迁移包 SHA-256 不匹配" }
tar.exe -xzf $Archive.Path
```

哈希不一致就停止，不要运行解压出的任何文件。

## 安装：只双击一次

进入解压出的 `intranet-browser-agent-transfer-1.0.10-core-windows-x64` 目录，只双击下面这一个文件：

```text
INSTALL-WINDOWS-PILOT.cmd
```

不需要先手工运行门禁脚本，也不需要安装结束后再运行第二个脚本。启动器会在同一个窗口依次完成“自动门禁 → 安装”：门禁只使用临时 Claude 配置，测试前后两次制品校验、完整测试、全部 PowerShell 脚本语法检查、假 Claude CLI 回滚测试、内层运行时临时解压、包内 Node 和 Playwright Extension 的来源/版本/哈希校验、真实 MCP stdio `initialize + tools/list` 握手和真实 Claude CLI 隔离探针全部通过后，才开始持久化安装；失败时不会触碰真实 Claude 用户配置。

无需 UAC，也不需要填写项目目录、网址、网络强制层或审批字段。向导把运行时、配置、输出和离线扩展文件安装到当前用户的 `%LOCALAPPDATA%\IntranetBrowserAgent`，自动识别 Chrome（优先）或 Edge（兜底），缺少扩展时先自动离线安装；仅当浏览器拒绝时才需要按屏幕提示加载一次包内目录，原安装进程会等待并续跑。随后向导将 `intranet-browser-agent` 注册为 Claude Code user-scope MCP。它不会创建专用 Profile，而是通过扩展连接用户明确选择的现有标签页，因此可以复用目标机 Chrome 已有 Cookie、SSO 登录态、客户端证书和已安装浏览器扩展。它不会询问或创建项目目录，不会写任何项目文件。

试点不配置网址白名单，Playwright MCP 按默认行为允许访问这台机器当前网络能够访问的全部网址。向导不会设置或询问防火墙，也不要求负责人、变更单、数据分类、模型路由或审批单号。

向导会自动完成以下工作：

1. 在临时目录运行 Windows 发布门禁，不修改真实 Claude 配置。
2. 检查 Windows x64 和 Python，校验迁移目录和内层运行包。
3. 校验包内官方 Playwright Extension CRX 的固定 ID、版本、大小、SHA-256、CRX3 结构、签名后的 Web Store 元数据和 `<all_urls>` 权限，并逐文件验证已解压副本；已安装则复用。未安装时先发布 CRX/已解压副本，生成只引用本机 `file:///` 的 update manifest 并尝试当前用户策略安装；未实际出现时恢复临时策略，打开扩展页给出唯一目录并等待人工加载，检测成功后自动进入第 4 步。
4. 在 `%LOCALAPPDATA%\IntranetBrowserAgent\staging\r-*` 的短路径同盘目录解压固定运行时，按发布批准哈希校验包内 Windows x64 Node.js 的来源、哈希和版本，通过后才发布版本目录；系统 Node.js 保持原状。
5. 自动生成 `extension` 模式部署清单和 Playwright 配置，显式绑定自动识别出的 `chrome`/`msedge` channel 及实际浏览器 `.exe`，先在 `%LOCALAPPDATA%` 内暂存并 preflight，再整目录切换；目标路径经过 link/junction 或越界时自动停止。浏览器使用自己的 last-used Profile，因此无需填写 Profile 或项目目录。
6. 先在临时目录自动演练首次安装、升级、条目/环境错写和 `remove/add/get` 失败回滚，再备份真实 Claude Code 用户配置，通过 `--scope user` 注册 MCP；注册项直接执行包内 `node.exe + 固定 cli.js + --browser=<channel> + --executable-path=<自动识别的 browser.exe> + config`，不依赖 `.cmd` shell shim，并核对实际 user-scope 命令、参数和固定环境。遗留的 Playwright MCP 配置覆盖变量、`NODE_OPTIONS`、`NODE_PATH` 不会改写包内配置，也不需要用户填写。失败时逐字节恢复用户配置、旧部署配置和本次新增的浏览器策略。
7. 先对安装后的最终路径执行 preflight，再使用与最终注册一致的直接命令完成 MCP stdio `initialize` 和 `tools/list` 握手，自动确认所有 user-scope 路径位于当前用户 `%LOCALAPPDATA%`、不经过子级 link/junction，并确认 extension 配置和浏览器 channel；出现任何 `FAIL` 就停止。全程不修改项目 `.mcp.json` 或 `CLAUDE.md`。

首次安装时 Claude 明确报告没有可删除的旧 user-scope MCP 条目是正常情况，向导会记录提示后继续注册；其他删除错误会自动恢复并停止，不需要手工执行任何 Claude/npm 命令。若没有找到可用的现有 Claude Code，向导会在任何真实配置变更前明确停止，不会自行安装。若你本来就设置了 `CLAUDE_CONFIG_DIR`，向导会自动使用它；为避免 Claude 把配置写进当前项目，它必须是本机盘符绝对路径，不能写相对路径、`~` 或 UNC。

成功后，重启 Claude Code，在任意项目中输入 `/mcp`，确认 `intranet-browser-agent` 已连接。第一次调用浏览器工具时，Playwright Extension 会显示连接页；选择一个已经登录的现有 Chrome 标签页或标签组。随后先只测试“读取当前页面标题”，不要提交、上传或删除。连接批准和标签页选择是访问边界，不是第二次安装。

向导不会运行 `npm install`、`pnpm install`、`npx`，不会修改系统 Node.js，也不会临时绕过 PowerShell 执行策略。若脚本被企业策略拦截，应走组织签名或脚本批准流程。

若自动门禁失败，窗口会直接显示日志尾部并给出 `%TEMP%\IntranetBrowserAgent\WINDOWS-RELEASE-GATE-*.log`；若安装阶段失败，则显示 `%TEMP%\IntranetBrowserAgent\INSTALL-WINDOWS-PILOT-*.log`。两类日志都会自动保留完整输出，直接提供对应日志即可定位，不需要为了收集信息重跑。
