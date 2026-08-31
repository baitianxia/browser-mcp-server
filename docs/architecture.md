# 架构规范

状态：当前、规范性文档。

## 目标

在企业内网终端或 VDI 上，为 Claude Code 提供可复用人工登录态的浏览器操作能力，同时最小化公网依赖、凭证暴露、横向访问和不可审计写操作。

## 非目标

- 不提供自动输入密码、OTP、Passkey 或验证码。
- 不承诺 Canvas、WebGL、远程桌面等纯视觉界面达到 DOM 页面同等可靠性。
- 不把 MCP 的 URL 过滤、提示词或 loopback 监听视为安全隔离。
- 不提供跨主机共享浏览器或多 Agent 争用同一 Profile。
- 不重新打包或签名上游 Chrome 扩展，也不替组织执行机器级/域级扩展策略发布；Windows pilot 只随已审核迁移包携带固定的官方 CRX 及其逐文件一致的已解压副本。
- 不在 V1 支持 Windows ARM64、UNC 部署根或网络共享 Profile。

## 组件与信任边界

```text
┌──────────────────── 企业终端 / VDI ────────────────────┐
│                                                       │
│  Claude Code ── stdio ── Playwright MCP               │
│       │                         │                     │
│       │ model route             │ local browser API   │
│       ▼                         ▼                     │
│  已批准模型网关             企业 Chrome                │
│                              专用 AI Profile           │
│                                   │                   │
└───────────────────────────────────┼───────────────────┘
                                    ▼
                          企业代理 / 防火墙 / 网络 ACL
                                    ▼
                         批准的内网站点与必要 SSO
```

上图是生产 persistent 基线。Windows 通用 pilot 使用 `Claude Code → stdio → Playwright MCP → Playwright Extension → 用户批准的现有 Tab`，不把整个日常 Profile 作为自动控制目标。

强制边界：

- 网络：企业代理、主机防火墙、ACL 或隔离 VDI。
- 身份：生产 persistent 使用专用 OS 用户（推荐）和专用 Chrome Profile；Windows pilot 由人完成认证，并以扩展的逐次连接批准和 Tab 选择限制本次暴露范围。
- 文件：Claude Code/MCP 客户端的工作区根和 OS 文件权限；Windows 使用 NTFS ACL，不把 POSIX mode 当成 ACL 证据。
- 模型数据：批准的企业模型路由及数据处理策略。
- 高风险动作：客户端确认或独立业务审批。

防误操作护栏：

- Playwright `allowedOrigins`。
- Agent 操作规范。
- MCP 输出目录大小限制、最小日志级别和默认不保存会话。

## 运行模式

### persistent（默认）

Playwright 启动企业 Chrome，使用部署清单指定的专用 `userDataDir`。该目录只能由一个 Agent 会话持有。用户首次启动时在有头浏览器中完成 SSO/MFA；后续复用 Profile。

不得把日常 Chrome 默认 Profile 路径配置为 `userDataDir`。

### extension

Playwright Extension 接管用户明确允许的现有 Tab。生产扩展必须通过批准的企业分发渠道安装。Windows 通用内网 pilot 固定使用此模式，以复用目标机 Chrome/Edge 当前 Profile 的登录态：迁移包携带固定 ID、版本、大小、SHA-256、CRX3/manifest/Web Store 元数据均经过校验的官方 CRX，以及与 CRX payload 逐文件一致的已解压目录。安装器先尝试当前用户本地 `file:///` 策略安装；浏览器未实际确认安装时，必须恢复该临时策略，打开扩展管理页、显示唯一的包内目录并在原进程中等待人工“加载已解压的扩展程序”，检测成功后才继续。人工回退的安装检测只接受指向该精确批准目录的扩展记录，不接受 CI 合成目录、复制目录或任意其他绝对路径。Windows 清单和 MCP 参数还必须绑定安装器实际识别的 Chrome/Edge `.exe`；固定 MCP 版本在未指定该路径时不能完整识别只记录在 `Secure Preferences` 的手工 unpacked 扩展。指定路径后浏览器不传 `--profile-directory`，因此安装检测只允许浏览器 `Local State.profile.last_used` 指向的 Profile 通过，防止在休眠 Profile 中找到扩展却启动另一个 Profile。默认保留每次连接批准和 Tab 选择 UI；不在仓库或用户 MCP 配置中保存扩展 token。

### cdp

Playwright 连接已开启远程调试的 Chrome。endpoint 只能是官方 channel 名或 loopback URL。禁止 `0.0.0.0`、局域网 IP、主机名和跨主机 WebSocket。

## MCP 配置

- 部署清单必须声明目标 `os` 和 `arch`；V1 的 Windows 目标固定为 x64，且只接受本机盘符绝对路径。
- 传输固定为 `stdio`。
- 命令必须是无需在线解析的绝对路径。macOS/Linux 使用离线运行包中的无扩展名 shell wrapper；Windows 必须直接执行清单 `nodeExecutable` 指向的已验证 `node.exe`，并把固定运行包内的 Playwright/DevTools CLI JavaScript 路径作为首个参数，不得把 `.cmd`/`.bat` shell shim 配成 MCP `command`。
- 禁止 `npx`、`@latest` 和运行时包下载。
- `allowUnrestrictedFileAccess` 固定为 `false`。
- project/managed scope 的 `workspaceRoots` 声明预期的 Claude Code 项目根，preflight 会核对。Windows 通用内网 pilot 使用 Claude Code `user` scope，`workspaceRoots=[]`，实际项目根由每次启动 Claude Code 的目录和 MCP roots 协商决定。
- 默认 `saveSession=false`、`console.level=warning`。
- Windows 通用内网 pilot 省略 `network` 配置；依据固定版本 Playwright MCP 的默认语义，这表示允许浏览当前主机网络可访问的全部网址。安装器不询问或修改防火墙。
- Windows `extension` 模式必须声明由安装器自动解析的本机 `.exe` 绝对路径 `browser.executablePath`；渲染、MCP 握手、Claude user-scope 注册和最终条目核对必须使用同一个 `--executable-path`，并同时保留精确 `--browser=chrome|msedge`。
- Windows Playwright MCP 条目必须带有 `config/windows-mcp-environment.json` 定义的精确环境映射：清空固定版本支持的配置覆盖变量以及 `NODE_OPTIONS`/`NODE_PATH`，仅把心跳超时固定为默认 `5000`。不得包含扩展连接 token、秘密或调用者提供的任意环境值；门禁握手和最终 user-scope 注册必须使用同一策略。
- production 若配置 `allowedOrigins`，每项必须是显式 origin；不接受全局通配符或带路径 URL。该过滤仍不是网络安全边界。
- 视觉坐标能力由 `controls.visionFallback` 显式开启。
- DevTools 由 `controls.devtools` 显式开启，并要求 loopback 调试 endpoint。

## 状态机

每个浏览器步骤必须遵循：

```text
Observe → Reason → Act(one step) → Wait/change detection
   ▲                                  │
   └────────── Re-observe ← Verify ───┘
```

导航、提交、弹窗、Tab 切换、AJAX 更新、SPA 路由和可能改变选中项的动作都会使旧快照失效。

## 发布模型

- 有网构建区从锁文件安装固定包，跳过依赖安装脚本和浏览器下载。
- pnpm 11 的布局设置位于 `runtime/pnpm-workspace.yaml`；Windows 使用 `nodeLinker: hoisted` 与 `packageImportMethod: copy`，归档前删除包管理器元数据并拒绝 link/reparse point。
- 构建后实际执行运行包所含 CLI 的 `--help` 冒烟验证；Playwright MCP 另执行无浏览器 stdio 协议握手。
- Windows x64 一键包必须携带最小 Node distribution，只能由官方 Windows x64 ZIP 经官方 `SHASUMS256.txt` 校验后白名单提取，并与 `config/windows-node-sources.json` 中当前发布批准的 archive、清单、`node.exe` 和 `LICENSE` 哈希逐项一致；运行目录只允许 `node.exe`、`LICENSE`、`VERSION` 和 `SOURCE.json`，明确排除 npm、npx、corepack 和其他上游文件。交叉构建主机不得把本机 Node 二进制复制进 Windows 包。
- Windows x64 pilot 迁移包必须在有网构建区下载 `config/playwright-extension-source.json` 批准的精确官方 CRX，验证固定 ID/版本/文件名/大小/SHA-256、CRX3 结构、manifest 公钥派生 ID、权限和 Web Store verified contents 后，安全提取 payload；CRX 与已解压目录必须同时进入外层完整性清单，目标机再次验证二者逐文件一致。目标机不得下载扩展。
- 生成 CycloneDX 组件清单、逐文件 SHA-256 和构建元数据；archive 相邻 `.sha256` 必须以显式 UTF-8/ASCII 字节写成 `<64 位小写 hex><两个空格><文件名><LF>`，不得随 Windows 文本模式转成 CRLF，以保证标准 `shasum -c` 可直接校验。
- 完整迁移包只组合已验证运行包和白名单部署工具链；不收录真实环境清单、版本库元数据、构建输出或本地缓存，并为外层 archive 再生成 SHA-256。
- Windows 迁移包顶层提供 pilot-only 双击向导。顶层 launcher 必须先自动运行 Windows 发布门禁，只有门禁通过才在同一个窗口继续验证制品、把运行时安装到当前用户 `%LOCALAPPDATA%\IntranetBrowserAgent`，再通过 Claude Code 官方 `--scope user` 注册 MCP，使其对该用户的所有项目可用。普通双击流程不得请求 UAC，也不得询问或创建项目目录、选择浏览器、配置网址白名单/防火墙或收集生产审批字段。若浏览器拒绝离线策略安装，唯一允许的交互是安装器自动打开扩展页后由用户加载屏幕显示的已解压目录；安装器必须等待并续跑，不得要求重新启动流程。
- Windows user-scope pilot 不写任何项目的 `.mcp.json`、`CLAUDE.md` 或其他仓库文件；Claude Code 用户配置变更前必须备份，升级失败必须恢复。安装器用当前用户目录中的独占锁阻止两个向导并发修改。版本目录不可覆盖；运行时必须先在同盘唯一暂存目录校验再发布，配置必须先暂存/preflight 后整目录切换，失败时恢复旧配置目录。包内 `node.exe` 刚完成验证时可能被 Defender、EDR 或索引进程短暂持有不共享删除的句柄；暂存运行时到最终版本目录必须使用 Windows PowerShell 5.1 中由 Win32 `MoveFile` 支撑的 `[IO.Directory]::Move` 做同盘原子发布，不能使用可能在失败前创建目标目录的 PowerShell FileSystem provider `Move-Item`。原子移动遇到 `UnauthorizedAccessException`、共享冲突或等价的目录移动 I/O 错误时，安装器必须在同一进程中按有界退避自动重试，明确记录等待与恢复，不得要求重新运行发布门禁。每次重试前必须确认源目录仍由本次安装持有且目标不存在；若移动可能已经完成，则按源/目标状态确认后继续并再次验证最终目录。目标已出现、源/目标状态不明确或重试窗口结束仍被拒绝时才失败关闭，且不得覆盖、合并或猜测性删除目录。不得下载依赖、运行包管理器、绕过 PowerShell 执行策略或自动处理登录秘密。
- Claude Code user-scope 注册不得直接由 PowerShell 拼接并执行多步事务；安装器必须调用 `register_claude_user_mcp.py`，由它按退出码执行 `remove → add → get`、在变更前逐字节备份用户配置，并在非“条目不存在”的 `remove` 错误、`add/get`、实际 user-scope 命令/参数/固定环境不匹配或执行异常时恢复。首次安装中 Claude 明确报告旧条目不存在才可忽略。注册器必须显式容错解码 Claude CLI 的 UTF-8 输出，并使状态/错误输出在窄 Windows 代码页下可表示，终端代码页不得成为配置事务的成败条件。Windows 自动流程只复用当前用户已经安装且可执行的 Claude Code，绝不安装、升级、替换或修复它。发布门禁与安装器必须共用同一个探测器：显式路径存在时只接受该路径；否则依次解析当前 `PATH` 中的 `claude.exe`、npm 生成的 `claude.cmd`，最后检查官方每用户位置 `%USERPROFILE%\.local\bin\claude.exe`。原生入口直接执行；npm 入口只能在同级标准 `node_modules\@anthropic-ai\claude-code\package.json` 声明合法 `claude` bin 且能解析现有 `node.exe` 时，转换为直接 `node.exe + 已安装 cli.js` 调用，不通过 `cmd.exe` 拼接参数，也不运行 npm/pnpm/npx。任一解析出的入口都必须实际通过 `--version` 和隔离注册探针。注册到 MCP 的服务命令仍直接指向迁移包内已验证的 `node.exe + Playwright cli.js`，与 Claude Code 自身的安装方式无关。若当前用户设置 `CLAUDE_CONFIG_DIR`，只接受本机盘符绝对路径，拒绝相对路径、`~` 和 UNC，防止把用户配置意外写进迁移/项目目录。
- 安装器在接触真实 Claude Code 用户配置前，必须在临时目录用假 Claude CLI 自动覆盖首次安装、成功 stderr、升级、`add` 失败、`get` 失败、条目错写和新配置删除回滚。正式放行前还必须在受控 Windows x64 的 Windows PowerShell 5.1 上执行 `verify-windows-release.ps1`；门禁只能校验自身所在的同一解压迁移目录，必须临时解压内层运行时，校验批准的 Node 来源并执行目标 `node.exe --version`，再用直接 `node.exe + cli.js + config` 完成 MCP stdio `initialize` 与 `tools/list` 握手。随后门禁须通过临时 `CLAUDE_CONFIG_DIR` 对真实 Claude CLI 执行隔离的 `remove/add/get` 探针并核对实际 user-scope 条目。门禁运行 Python 及其子进程时必须禁止写入 bytecode，并在测试前后各校验一次迁移包，证明自检没有污染解压目录。尚无预先门禁证据的交叉构建包只能明确标为自检候选，并由顶层 launcher 在任何持久化安装之前强制执行同一门禁；它不得冒充正式制品。
- user-scope Windows pilot 的写入前路径检查与 preflight 必须自动证明运行时、`nodeExecutable`、配置、输出、CRX 和已解压扩展目录均位于当前用户 `%LOCALAPPDATA%`，且不经过该根目录以下的 link/junction；并证明清单和已部署 Playwright 配置使用 `extension` 模式、绑定自动选择的浏览器 channel 与实际浏览器可执行文件、未配置 `userDataDir` 且保留人工连接批准。这些可计算事实不得转嫁为安装人员问答。
- `production` 清单继续要求独立网络强制层及模型数据边界记录；这些是生产放行条件，不由试点安装器收集或代填。
- Windows 生产包必须在 Windows x64 受控构建机使用 PowerShell 构建。非 Windows 主机只可生成 `core` 试点候选包；允许携带经过来源、归档哈希、文件白名单和 AMD64 PE 校验的目标 Windows Node，但仍须在元数据标记交叉构建和未完成目标 CLI 冒烟，目标机验收前不得放行生产。
- 企业扫描、签名和制品库上传由组织流水线完成；本项目不伪造这些外部结果。
- 内网先验 SHA-256，再解压到版本目录，通过 `current` 指针切换；回滚只切回上一已验证版本。

## 变更规则

以下变化必须同时更新本文件、部署 Schema、校验器和测试：运行模式、信任边界、默认权限、版本、配置字段、风险动作集合或放行条件。
