# Windows 内网试点：解压后只双击一次

状态：当前；适用于已通过发布门禁的 Windows x64 试点包，以及明确标记的自检候选包。自检候选不能冒充正式制品，但顶层启动器会在任何持久化安装之前强制运行同一门禁。生产部署仍以 `operations.md` 为准。

目标机只需准备：Windows x64、原生 Claude Code `claude.exe`、Chrome 或 Edge、Python 3.10+。当前迁移包自带固定 Node.js，系统已有的 v20.18.3 可以保留，不需要安装、升级或修复 Node/npm；旧式 npm 安装产生的 `claude.cmd` 不属于本一键流程。迁移包必须放在本机盘符目录，不使用 UNC。

## 文件准备：校验并解压

把迁移包和相邻 `.sha256` 放进一个新的空目录，在该目录打开 PowerShell：

```powershell
$Archive = Resolve-Path .\intranet-browser-agent-transfer-1.0.8-core-windows-x64.tar.gz
$Expected = (((Get-Content "$($Archive.Path).sha256" -Raw) -split '\s+')[0]).ToLowerInvariant()
$Actual = (Get-FileHash -LiteralPath $Archive.Path -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Actual -ne $Expected) { throw "迁移包 SHA-256 不匹配" }
tar.exe -xzf $Archive.Path
```

哈希不一致就停止，不要运行解压出的任何文件。

## 安装：只双击一次

进入解压出的 `intranet-browser-agent-transfer-1.0.8-core-windows-x64` 目录，只双击下面这一个文件：

```text
INSTALL-WINDOWS-PILOT.cmd
```

不需要先手工运行门禁脚本，也不需要安装结束后再运行第二个脚本。启动器会在同一个窗口依次完成“自动门禁 → 安装”：门禁只使用临时 Claude 配置，测试前后两次制品校验、完整测试、全部 PowerShell 脚本语法检查、假 Claude CLI 回滚测试、内层运行时临时解压、包内 Node 来源/版本校验、真实 MCP stdio `initialize + tools/list` 握手和真实 Claude CLI 隔离探针全部通过后，才开始持久化安装；失败时不会触碰真实 Claude 用户配置。

无需 UAC，也不需要填写任何内容。向导把运行时、配置、输出和专用浏览器 Profile 安装到当前用户的 `%LOCALAPPDATA%\IntranetBrowserAgent`，自动识别 Chrome（优先）或 Edge（兜底），再将 `intranet-browser-agent` 注册为 Claude Code user-scope MCP。它不会询问或创建项目目录，不会写任何项目文件。

试点不配置网址白名单，Playwright MCP 按默认行为允许访问这台机器当前网络能够访问的全部网址。向导不会设置或询问防火墙，也不要求负责人、变更单、数据分类、模型路由或审批单号。

向导会自动完成以下工作：

1. 在临时目录运行 Windows 发布门禁，不修改真实 Claude 配置。
2. 检查 Windows x64 和 Python，校验迁移目录和内层运行包。
3. 在 `%LOCALAPPDATA%\IntranetBrowserAgent\staging\r-*` 的短路径同盘目录解压固定运行时，按发布批准哈希校验包内 Windows x64 Node.js 的来源、哈希和版本，通过后才发布版本目录。
4. 保留系统 Node.js 原状，在当前用户 `%LOCALAPPDATA%` 安装版本化运行时。
5. 自动生成合法部署清单和 Playwright 配置，先在 `%LOCALAPPDATA%` 内暂存并 preflight，再整目录切换；目标路径经过 link/junction 或越界时自动停止。
6. 先在临时目录自动演练首次安装、升级、条目/环境错写和 `remove/add/get` 失败回滚，再备份真实 Claude Code 用户配置，通过 `--scope user` 注册 MCP；注册项直接执行包内 `node.exe + 固定 cli.js`，不依赖 `.cmd` shell shim，并核对实际 user-scope 命令、参数和固定环境。遗留的 Playwright MCP 配置覆盖变量、`NODE_OPTIONS`、`NODE_PATH` 不会改写包内配置，也不需要用户填写。失败时逐字节恢复用户配置和旧部署配置。
7. 先对安装后的最终路径执行 preflight，再使用直接 `node.exe + cli.js + config` 完成 MCP stdio `initialize` 和 `tools/list` 握手，自动确认所有 user-scope 路径位于当前用户 `%LOCALAPPDATA%`、不经过子级 link/junction，且使用专用非默认 Profile；出现任何 `FAIL` 就停止。全程不修改项目 `.mcp.json` 或 `CLAUDE.md`。

首次安装时 Claude 明确报告没有可删除的旧 user-scope MCP 条目是正常情况，向导会记录提示后继续注册；其他删除错误会自动恢复并停止，不需要手工执行任何 Claude/npm 命令。若你本来就设置了 `CLAUDE_CONFIG_DIR`，向导会自动使用它；为避免 Claude 把配置写进当前项目，它必须是本机盘符绝对路径，不能写相对路径、`~` 或 UNC。

成功后，重启 Claude Code，在任意项目中输入 `/mcp`，确认 `intranet-browser-agent` 已连接。由人完成 SSO/MFA；第一次只测试“打开一个内网页面并读取页面标题”，不要提交、上传或删除。

向导不会运行 `npm install`、`pnpm install`、`npx`，不会修改系统 Node.js，也不会临时绕过 PowerShell 执行策略。若脚本被企业策略拦截，应走组织签名或脚本批准流程。

若自动门禁失败，窗口会直接显示日志尾部并给出 `%TEMP%\IntranetBrowserAgent\WINDOWS-RELEASE-GATE-*.log`；若安装阶段失败，则显示 `%TEMP%\IntranetBrowserAgent\INSTALL-WINDOWS-PILOT-*.log`。两类日志都会自动保留完整输出，直接提供对应日志即可定位，不需要为了收集信息重跑。
