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
│  Claude Code ── stdio ── 兼容层 ── Playwright MCP     │
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

上图是生产 persistent 基线。兼容层与固定上游 Playwright MCP 位于同一离线运行包，只做 ADR-0010 定义的快照压缩、页面稳定等待和同目标交互回退，不改变权限边界。Windows 通用 pilot 初始使用 `Claude Code → stdio → 兼容层 → Playwright MCP → Playwright Extension → 用户批准的现有浏览器会话`；安装后可选择保存当前用户扩展授权，或切换到不读取日常登录态的独立 Profile。

强制边界：

- 网络：企业代理、主机防火墙、ACL 或隔离 VDI。
- 身份：production persistent 使用专用 OS 用户（推荐）和专用 Chrome Profile；Windows pilot 由人完成认证，初始以扩展逐次批准和选择限制暴露范围。保存当前用户扩展令牌会扩大持久授权；独立 Profile 则隔离日常浏览器登录态。
- 文件：Claude Code/MCP 客户端的工作区根和 OS 文件权限；Windows 使用 NTFS ACL，不把 POSIX mode 当成 ACL 证据。
- 模型数据：批准的企业模型路由及数据处理策略。
- 高风险动作：客户端确认或独立业务审批。

防误操作护栏：

- Playwright `allowedOrigins`。
- Agent 操作规范。
- MCP 输出目录大小限制、最小日志级别和默认不保存会话。

## 运行模式

### persistent（生产默认）

Playwright 启动企业 Chrome，使用部署清单指定的专用 `userDataDir`。该目录只能由一个 Agent 会话持有。用户首次启动时在有头浏览器中完成 SSO/MFA；后续复用 Profile。

不得把日常 Chrome 默认 Profile 路径配置为 `userDataDir`。Windows user-scope pilot 安装后可通过设置工具切换到 `%LOCALAPPDATA%\IntranetBrowserAgent\browser-profile\pilot` 的独立 Profile，并选择有头或无头；首次需要 SSO/MFA 时必须先用有头模式。

### extension

Playwright Extension 接管用户允许的现有浏览器会话。生产扩展必须通过批准的企业分发渠道安装。Windows 通用内网 pilot 的初始模式固定为 Extension，以复用目标机 Chrome/Edge 当前 Profile 的登录态：迁移包携带固定 ID、版本、大小、SHA-256、CRX3/manifest/Web Store 元数据均经过校验的官方 CRX，以及与 CRX payload 逐文件一致的已解压目录。安装器先尝试当前用户本地 `file:///` 策略安装；更新清单不可写、策略键不可读/不可创建/不可写、企业 ACL 拒绝或自动启动浏览器失败都只表示这项可选自动化不可用，必须记录原因并直接进入人工加载，不能终止安装。只有自动策略已经实际写入、但安装器无法恢复其原值或删除本次值时，才因状态不安全而停止。浏览器未实际确认安装时同样必须恢复已写入的临时策略，尽力打开扩展管理页、显示扩展页地址和唯一包内目录，并在原进程中等待人工“加载已解压的扩展程序”；自动打开页面或复制目录到剪贴板失败也不得中止等待。检测成功后才继续。人工回退的安装检测只接受指向该精确批准目录的扩展记录，不接受 CI 合成目录、复制目录或任意其他绝对路径。Windows 清单和 MCP 参数还必须绑定安装器实际识别的 Chrome/Edge `.exe`；安装检测只允许浏览器 `Local State.profile.last_used` 指向的 Profile 通过。

初始设置保留每次连接批准。安装后用户可选择官方扩展提供的当前用户令牌：令牌需要由用户从扩展页复制一次，设置工具不得尝试解析或改写浏览器 Profile 存储；令牌只写入 Claude Code 当前用户 MCP 环境，不写入仓库、迁移包、部署清单或日志。该令牌会绕过后续连接对话框并扩大持久授权范围，切回逐次批准或独立 Profile 时必须从 MCP 环境删除。Extension 模式始终有头，不能与无头组合。

### cdp

Playwright 连接已开启远程调试的 Chrome。endpoint 只能是官方 channel 名或 loopback URL。禁止 `0.0.0.0`、局域网 IP、主机名和跨主机 WebSocket。

## MCP 配置

- 部署清单必须声明目标 `os` 和 `arch`；V1 的 Windows 目标固定为 x64，且只接受本机盘符绝对路径。
- 传输固定为 `stdio`。
- 命令必须是无需在线解析的绝对路径。macOS/Linux 使用离线运行包中的无扩展名 shell wrapper；Windows 必须直接执行清单 `nodeExecutable` 指向的已验证 `node.exe`，并把固定运行包内的 `bin/intranet-browser-agent-mcp.js` 作为 Playwright 首个参数；该兼容层再启动同包固定上游 CLI。不得把 `.cmd`/`.bat` shell shim 配成 MCP `command`。
- 禁止 `npx`、`@latest` 和运行时包下载。
- `allowUnrestrictedFileAccess` 固定为 `false`。
- project/managed scope 的 `workspaceRoots` 声明预期的 Claude Code 项目根，preflight 会核对。Windows 通用内网 pilot 使用 Claude Code `user` scope，`workspaceRoots=[]`，实际项目根由每次启动 Claude Code 的目录和 MCP roots 协商决定。
- 默认 `saveSession=false`、`console.level=warning`。
- Windows 通用内网 pilot 省略 `network` 配置；依据固定版本 Playwright MCP 的默认语义，这表示允许浏览当前主机网络可访问的全部网址。安装器不询问或修改防火墙。
- Windows `extension` 及 user-scope pilot 的 `persistent` 模式必须声明自动解析的本机 `.exe` 绝对路径 `browser.executablePath`；渲染、MCP 握手、Claude user-scope 注册和最终条目核对必须使用同一个 `--executable-path`，并同时保留精确 `--browser=chrome|msedge`。
- Windows Playwright MCP 条目必须带有 `config/windows-mcp-environment.json` 定义的精确环境映射：清空固定版本支持的配置覆盖变量以及 `NODE_OPTIONS`/`NODE_PATH`，仅把心跳超时固定为默认 `5000`。初始安装、逐次批准和独立 Profile 不得增加任意环境值；只有用户明确选择当前用户扩展授权时，允许额外增加一个格式经过验证的 `PLAYWRIGHT_MCP_EXTENSION_TOKEN`。门禁、握手、注册和最终条目核对必须使用与所选模式一致的精确策略。
- production 若配置 `allowedOrigins`，每项必须是显式 origin；不接受全局通配符或带路径 URL。该过滤仍不是网络安全边界。
- 视觉坐标能力由 `controls.visionFallback` 显式开启。
- DevTools 由 `controls.devtools` 显式开启，并要求 loopback 调试 endpoint。
- `interaction.snapshotStrategy` 只允许 `compact|full`；Windows pilot 默认 `compact`，以关闭上游隐式完整快照并由兼容层返回内联限深、最多 16000 字符的快照，超限后引导使用 `browser_find`、目标局部快照或 `full`。`interaction.compatibilityMode` 只允许 `robust|standard`；`robust` 按 ADR-0010 增强动态页面，`standard` 透传上游动作。两项都必须渲染进 `interaction.config.json` 并可由安装后设置工具事务化切换。

## 状态机

每个浏览器步骤必须遵循：

```text
Observe → Reason → Act(one step) → Wait/change detection
   ▲                                  │
   └────────── Re-observe ← Verify ───┘
```

导航、提交、弹窗、Tab 切换、AJAX 更新、SPA 路由和可能改变选中项的动作都会使旧快照失效。`robust` 模式中的导航与 `browser_click_and_wait` 必须在同一工具调用中等待可验证条件或 DOM 安静，并返回新的精简快照；不得返回空文件或用固定 sleep 假定完成。原生点击失败后的 DOM 回退只能作用于同一个唯一、连接、可见且未禁用的目标，不得扩大高风险动作权限。

## 发布模型

- 有网构建区从锁文件安装固定包，跳过依赖安装脚本和浏览器下载。
- pnpm 11 的布局设置位于 `runtime/pnpm-workspace.yaml`；Windows 使用 `nodeLinker: hoisted` 与 `packageImportMethod: copy`，归档前删除包管理器元数据并拒绝 link/reparse point。
- 构建后实际执行运行包所含 CLI 的 `--help` 冒烟验证；Playwright MCP 另执行无浏览器 stdio 协议握手。
- Windows x64 一键包必须携带最小 Node distribution，只能由官方 Windows x64 ZIP 经官方 `SHASUMS256.txt` 校验后白名单提取，并与 `config/windows-node-sources.json` 中当前发布批准的 archive、清单、`node.exe` 和 `LICENSE` 哈希逐项一致；运行目录只允许 `node.exe`、`LICENSE`、`VERSION` 和 `SOURCE.json`，明确排除 npm、npx、corepack 和其他上游文件。交叉构建主机不得把本机 Node 二进制复制进 Windows 包。
- Windows x64 pilot 迁移包必须在有网构建区下载 `config/playwright-extension-source.json` 批准的精确官方 CRX，验证固定 ID/版本/文件名/大小/SHA-256、CRX3 结构、manifest 公钥派生 ID、权限和 Web Store verified contents 后，安全提取 payload；CRX 与已解压目录必须同时进入外层完整性清单，目标机再次验证二者逐文件一致。目标机不得下载扩展。
- 生成 CycloneDX 组件清单、逐文件 SHA-256 和构建元数据；archive 相邻 `.sha256` 必须以显式 UTF-8/ASCII 字节写成 `<64 位小写 hex><两个空格><文件名><LF>`，不得随 Windows 文本模式转成 CRLF，以保证标准 `shasum -c` 可直接校验。
- 完整迁移包只组合已验证运行包和白名单部署工具链；不收录真实环境清单、版本库元数据、构建输出或本地缓存，并为外层 archive 再生成 SHA-256。
- Windows 迁移包顶层提供 pilot-only 双击向导。发布流水线必须在上传制品前完成 Windows 发布门禁；目标机顶层 launcher 只允许启动一次安装器，不得运行单元测试、PowerShell AST 全量扫描、假 Claude CLI 故障矩阵或任何其他发布者测试。安装器验证正式制品元数据后，把运行时安装到当前用户 `%LOCALAPPDATA%\IntranetBrowserAgent`，再通过 Claude Code 官方 `--scope user` 注册 MCP，使其对该用户的所有项目可用。普通双击流程不得请求 UAC，也不得询问或创建项目目录、选择浏览器、配置网址白名单/防火墙或收集生产审批字段。若离线策略准备、访问或浏览器确认任一步不可用，唯一允许的交互是由用户在扩展页加载屏幕显示的已解压目录；自动打开页面和复制路径只是非致命便利。安装器必须在原进程等待并续跑，不得要求重新启动流程。
- Windows 门禁通过后，流水线必须从已验证的迁移目录生成单顶层目录的 `windows-x64-ready.zip`，解压并按逐文件路径、长度、SHA-256 证明它只是无内容改写的重新封装。面向用户的 Actions artifact 直接上传该单一 ZIP 文件且关闭 artifact 二次归档，使下载文件本身就是“解压一次后双击”的成品；供发布方复核和企业扫描的 `tar.gz + .sha256` 继续单独保留，不能把两种交付形态混为一包。
- Windows user-scope pilot 不写任何项目的 `.mcp.json`、`CLAUDE.md` 或其他仓库文件；Claude Code 用户配置变更前必须备份，升级失败必须恢复。安装器用当前用户目录中的独占锁阻止两个向导并发修改。版本目录不可覆盖；运行时和扩展必须先在同盘唯一暂存目录校验再发布，配置必须先暂存/preflight 后整目录切换，失败时恢复旧配置目录。包内 `node.exe`、扩展或配置文件可能被 Defender、EDR 或索引进程短暂持有不共享删除的句柄；运行时发布、扩展发布/隔离、配置备份/发布及配置回滚必须共用 Windows PowerShell 5.1 中由 Win32 `MoveFile` 支撑的 `[IO.Directory]::Move` 同盘原子移动，不得使用可能在失败前创建目标目录的 PowerShell FileSystem provider `Move-Item`。原子移动遇到 `UnauthorizedAccessException`、共享冲突或等价目录移动 I/O 错误时，安装器必须在同一进程中按有界退避自动重试，明确记录等待与恢复，不得要求重新运行整个安装流程。每次重试前必须确认源仍存在且目标不存在；若移动可能已经完成，则按源/目标状态确认后继续并再次验证最终目录。目标已出现、源/目标状态不明确或重试窗口结束仍被拒绝时才失败关闭，且不得覆盖、合并或猜测性删除目录。已有扩展版本目录校验失败时必须先原子隔离到唯一备份路径，再从迁移包重建；配置事务失败时必须把本次配置原子隔离到备份目录后恢复旧目录，不得递归删除可能处于不明确状态的目录。不得下载依赖、运行包管理器、绕过 PowerShell 执行策略或自动处理登录秘密。
- 安装器还必须把版本化设置工具安装到当前用户目录，并提供稳定入口 `%LOCALAPPDATA%\IntranetBrowserAgent\BROWSER-AGENT-SETTINGS.cmd`。设置工具与安装器共用安装锁、路径检查、暂存/preflight、MCP 握手、原子配置切换和 Claude 注册事务；失败必须恢复旧配置。它只允许 ADR-0009 定义的三种浏览器组合，不得把 Extension 与无头或独立 Profile 与扩展授权拼接成无效状态；同时允许 ADR-0010 定义的两组交互选项独立切换。
- Claude Code user-scope 注册不得直接由 PowerShell 拼接并执行多步事务；安装器必须调用 `register_claude_user_mcp.py`，由它按退出码执行 `remove → add → get`、在变更前逐字节备份用户配置，并在非“条目不存在”的 `remove` 错误、`add/get`、实际 user-scope 命令/参数/固定环境不匹配或执行异常时恢复。首次安装中 Claude 明确报告旧条目不存在才可忽略。注册器必须显式容错解码 Claude CLI 的 UTF-8 输出，并使状态/错误输出在窄 Windows 代码页下可表示，终端代码页不得成为配置事务的成败条件。Windows 自动流程只复用当前用户已经安装且可执行的 Claude Code，绝不安装、升级、替换或修复它。发布门禁与安装器必须共用同一个探测器：显式路径存在时只接受该路径；否则依次解析当前 `PATH` 中的 `claude.exe`、npm 生成的 `claude.cmd`，最后检查官方每用户位置 `%USERPROFILE%\.local\bin\claude.exe`。原生入口直接执行；npm 入口只能在同级标准 `node_modules\@anthropic-ai\claude-code\package.json` 声明合法 `claude` bin 且能解析现有 `node.exe` 时，转换为直接 `node.exe + 已安装 cli.js` 调用，不通过 `cmd.exe` 拼接参数，也不运行 npm/pnpm/npx。安装器必须实际通过现有入口的 `--version`，发布门禁还必须执行隔离注册探针。注册到 MCP 的服务命令直接指向迁移包内已验证的 `node.exe + bin\intranet-browser-agent-mcp.js`，兼容层再启动同包上游 Playwright CLI；这与 Claude Code 自身的安装方式无关。若当前用户设置 `CLAUDE_CONFIG_DIR`，只接受本机盘符绝对路径，拒绝相对路径、`~` 和 UNC，防止把用户配置意外写进迁移/项目目录。
- 假 Claude CLI 对首次安装、成功 stderr、升级、`add`/`get` 失败、条目错写和新配置删除回滚的故障矩阵属于发布测试，不得在目标安装器运行。正式放行前必须在受控 Windows x64 的 Windows PowerShell 5.1 上对待上传制品执行 `verify-windows-release.ps1`；门禁只能校验自身所在的同一解压迁移目录，必须运行完整测试和 PowerShell AST 解析，临时解压内层运行时，校验批准的 Node 来源并执行目标 `node.exe --version`，再用直接 `node.exe + bin\intranet-browser-agent-mcp.js + 同包上游 CLI + config` 完成 MCP stdio `initialize` 与 `tools/list` 握手。随后门禁须通过临时 `CLAUDE_CONFIG_DIR` 对真实 Claude CLI 执行隔离的 `remove/add/get` 探针并核对实际 user-scope 条目。门禁运行 Python 及其子进程时必须禁止写入 bytecode，并在测试前后各校验一次迁移包。只有门禁与单次目标安装流水线都成功才可上传制品；目标 launcher 不得调用该门禁。目标安装器只接受 `buildHost=windows/x64`、`target=windows/x64`、`crossBuilt=false` 且 `targetCliSmokeTested=true` 的正式制品。
- user-scope Windows pilot 的写入前路径检查与 preflight 必须自动证明运行时、`nodeExecutable`、配置、输出、CRX、已解压扩展目录和可选独立 Profile 均位于当前用户 `%LOCALAPPDATA%`，且不经过该根目录以下的 link/junction。初始安装必须证明 `extension + 有头 + 每次批准`；设置变更必须证明 Extension 没有 `userDataDir/headless`，或 persistent 精确使用受管独立 Profile 与显式有头/无头值。这些可计算事实不得转嫁为安装人员问答。
- `production` 清单继续要求独立网络强制层及模型数据边界记录；这些是生产放行条件，不由试点安装器收集或代填。
- Windows 一键安装包必须在 Windows x64 受控构建机使用 PowerShell 构建。非 Windows 主机只可生成用于制品结构审查的 `core` 交叉构建候选包；它必须标记交叉构建和未完成目标 CLI 冒烟，目标 launcher 会拒绝安装，不能交给内网用户补做发布验证。
- 企业扫描、签名和制品库上传由组织流水线完成；本项目不伪造这些外部结果。
- 内网先验 SHA-256，再解压到版本目录，通过 `current` 指针切换；回滚只切回上一已验证版本。

## 变更规则

说明：本节较早条款中的“`node.exe + Playwright cli.js`”或“`node.exe + cli.js`”自 ADR-0010 起统一解释为 `node.exe + bin\intranet-browser-agent-mcp.js + 同包上游 Playwright CLI`；Claude Code npm 安装自身的 `node.exe + 已安装 cli.js` 探测规则不变。

以下变化必须同时更新本文件、部署 Schema、校验器和测试：运行模式、信任边界、默认权限、版本、配置字段、风险动作集合或放行条件。
