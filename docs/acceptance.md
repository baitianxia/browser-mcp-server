# 验收标准

状态：当前、规范性文档。

生产放行要求所有自动项通过，所有人工项有证据和负责人。一次失败不能靠提示词例外豁免。

## 自动验收

- `python3 -m unittest discover -s tests -v` 全部通过。
- 兼容层必须有独立 JSON-RPC 子进程测试，覆盖 `tools/list`、精简快照内联且非空、16000 字符硬上限、空快照内部重试、导航 DOM 稳定等待、原生点击成功不回退、仅 actionability 失败才同目标 DOM 回退、只读普通字段快速失败、自定义下拉选择、tooltip 属性/事件读取、点击后条件确认，以及 `standard/full` 完整透传。测试不得用固定 sleep 冒充页面结果。
- 本文较早验收条款中的目标 MCP“`node.exe + cli.js`”自 ADR-0010 起均指 `node.exe + bin\intranet-browser-agent-mcp.js + 同包上游 Playwright CLI`；描述 npm Claude Code 自身入口的文字不受影响。
- 每次推送到 `main` 的 `.github/workflows/windows-release.yml` 必须在 `windows-2022`、`windows-latest` 两个 Windows x64 runner 上以 Windows PowerShell 5.1 跑完源码测试和全部 PowerShell AST 解析；Windows 原生打包 job 还必须使用批准的 Node v24.19.0 来源和固定 pnpm 11.19.0 构建迁移包。job 只能在一次性有网 CI VM 内准备固定 Claude Code 2.1.84 的原生与 npm 两种测试夹具，且文档和日志必须明确这些不是目标机安装步骤。原生 `claude.exe` 必须先完成一次临时 `CLAUDE_CONFIG_DIR` 下的完整发布门禁；随后顶层 `INSTALL-WINDOWS-PILOT.cmd` 必须只启动一次安装器，并在原生入口已从 `PATH` 隐藏、真实 npm `claude.cmd` 可解析的环境中完成安装、注册和后置功能验证。目标 launcher 和安装日志不得包含单元测试发现器、PowerShell AST 全量扫描或注册器 `self-test`；共享探测器必须证明它选择 npm 入口并转换为现有 `node.exe + 已安装 cli.js` 直接调用，不通过 `cmd.exe` 拼接注册参数。同时必须阻断该 Node 与 Chrome 的 Internet 出站并保留 loopback，以证明目标流程不执行在线安装或修复。GitHub 托管 runner 没有可代表真人的受信任桌面输入，不能自动完成 Chrome 原生目录选择框，因此自动验收必须明确拆开这个人工边界：源码回归验证安装器先恢复临时策略、显示唯一目录、默认无限等待以及检测到扩展后在同一进程续跑；package job 进入该等待点后，只允许用 pipe-only `Extensions.loadUnpacked` 把精确批准目录加载到同一个仍存活的隔离 Chrome 会话，核对固定 ID、版本、启用状态和精确路径，并证明原 launcher 自动续跑。这个 Chrome 会话不得带 `--enable-automation`，必须保留单实例 URL 转发，使安装后的 MCP 能把扩展 `connect.html` 请求交给同一个已加载扩展的进程。该会话替身只验证等待/续跑状态机和后续功能，不得声称 CI 点过原生目录选择框，不得声称该加载在 Chrome 重启后持久，也不得替代目标机屏幕上明确给出的三步人工操作。安装完成后，在同一个 Internet 出站仍被阻断的 Chrome 会话中，必须由实际安装的 `node.exe + bin\intranet-browser-agent-mcp.js + 同包 @playwright/mcp` 调用 `browser_navigate`、精简快照及 ADR-0010 兼容工具访问本机离线页面，并证明页面收到预置到该 Profile 的会话 Cookie；CI 必须从隔离 Profile 的扩展状态页读取扩展自身已经生成并由后台使用的一次性令牌，只注入该次测试子进程，不得在扩展初始化后覆盖令牌，也不得把令牌写入迁移包、安装配置、日志或证据。最终必须分别保留发布门禁 PASS、npm 入口下单次安装 SUCCESS、安装摘要、user-scope 配置和安装后运行时完整性证据，并断言安装日志没有发布自检标记。失败 run 的产物不得作为 Windows 已验证迁移包交付。
- Windows 原生 package job 必须在精确的 Chrome `HKCU\Software\Policies\Google\Chrome\ExtensionInstallForcelist` 键上保留原 ACL，并对当前用户真实添加 `SetValue` 拒绝规则；确认操作系统实际拒绝写值后，才启动顶层 launcher。安装日志必须在同一进程依次出现 `OFFLINE EXTENSION POLICY UNAVAILABLE`、`MANUAL EXTENSION LOAD REQUIRED` 和最终 `SUCCESS`，且 `Windows pilot installer started` 仍只出现一次。进入人工等待后必须逐字恢复原 ACL；源码回归还必须证明更新清单写入、策略访问、自动打开扩展页或 `clip.exe` 失败均不绕过精确扩展检测，也不要求重启安装。
- 同一个 Windows package job 必须在启动 launcher 前预置一个缺少批准 CRX/已解压内容的旧扩展版本目录。安装器必须把该目录原子移动到唯一备份路径、逐字保留故障标记、从迁移包重建并重新验证正式目录；安装日志必须包含 `EXTENSION INVALID DIRECTORY QUARANTINED`，最终目录不得保留故障标记。
- 上述真实 npm Claude Code 2.1.84 夹具必须运行在目标机已报告的 Node v20.18.3 上；不能只用构建 Node v24.19.0 代替目标形态。
- 同一个 Windows package job 在一键安装和扩展登录态复用 E2E 通过后，必须调用 `%LOCALAPPDATA%` 中实际安装的设置脚本依次验证：保存格式正确的临时当前用户扩展令牌并核对 Claude user-scope 条目；切换独立 Profile 无头模式并在阻断 Chrome Internet 出站时完成真实 `navigate + snapshot`，证明原扩展 Profile 的会话 Cookie 没有被复用；切换独立 Profile 有头模式并核对渲染值；最后恢复 `extension + 有头 + 每次批准` 并证明令牌已经删除。随后使用标准 npm 形态的故障 Claude CLI，让 `remove/add` 实际改写后在 `get` 失败，必须逐字节恢复部署目录和 Claude 用户配置，且日志不得含令牌。临时令牌和独立 Profile 均不得进入上传证据；任何一步失败都不得上传正式包。
- 生产清单 `validate` 返回 `VALID`，且没有占位符。
- 完整迁移包、包内运行 archive 及二者的解压目录均通过 `verify-bundle.py`。
- Windows 运行包的 `SYMLINKS.json` 为空，不含 symlink、junction、reparse point、pnpm 元数据或 `.node`/DLL 原生依赖。唯一允许的 EXE 是 `node/node.exe`；它必须属于声明了 `bundledNode=true` 的最小 Node distribution。
- 最小 Node distribution 精确包含 `node.exe`、`LICENSE`、`VERSION`、`SOURCE.json`，通过官方 archive/`SHASUMS256.txt`/`win-x64/node.exe` SHA-256、逐文件哈希、Node.js 20.19+ 和 AMD64 PE 校验，并与 `config/windows-node-sources.json` 的当前批准哈希一致；自洽但未批准的来源必须失败。不含 npm、npx、pnpm、corepack 或安装脚本。
- Windows pilot 迁移包必须包含 `config/playwright-extension-source.json` 批准的精确官方 CRX 和已解压副本；构建与目标验证必须核对固定 ID/版本/文件名/大小/SHA-256、CRX3、manifest 公钥派生 ID、权限、Web Store verified contents，并证明已解压目录与 CRX payload 文件集合及逐文件哈希完全一致。目标机不得联网下载扩展。
- 迁移包只含逐文件白名单工具链和运行包；不含 `.git`、真实环境清单、构建输出、包管理缓存或 Python bytecode。
- `preflight` 无 `FAIL`；安装包版本与固定版本完全一致。
- `core` 包不含 `chrome-devtools-mcp`。
- 生成的 `.mcp.json` 不含 `npx`、`@latest`、HTTP MCP endpoint、扩展 token 或登录秘密。
- Windows `.mcp.json` 只使用安全的本机盘符绝对路径；`command` 必须直接指向清单声明、运行包内已验证的 `node.exe`，首个参数指向固定 `bin\intranet-browser-agent-mcp.js`；兼容层必须只启动同包固定 `node_modules\@playwright\mcp\cli.js`。Windows extension 和 pilot 独立 Profile 条目还必须精确包含自动识别 channel 的 `--browser` 和同一实际 `.exe` 的 `--executable-path`，不得使用 `.cmd`/`.bat` shell shim、UNC 或 POSIX 路径。
- Windows Playwright MCP 的 `.mcp.json` 和门禁握手环境必须使用 `config/windows-mcp-environment.json` 的同一精确映射，清空固定版本支持的配置覆盖变量及 `NODE_OPTIONS`/`NODE_PATH`，不得携带扩展 token 或秘密。最终 user-scope 条目默认也必须与该映射完全一致；只有用户明确选择当前用户扩展授权时，允许精确增加一个格式经过验证的 `PLAYWRIGHT_MCP_EXTENSION_TOKEN`，切回逐次批准或独立 Profile 后必须删除。条目核对必须拒绝任何其他缺少、增加或改写。
- production 的 Playwright 配置固定 `allowUnrestrictedFileAccess=false`，存在非空 `allowedOrigins`，且不保存会话（除非清单有明确保留策略）。
- Windows 试点向导生成的最小清单必须是 `mcpScope=user`、`workspaceRoots=[]`，并省略网址白名单、防火墙和生产审批字段。渲染出的 Playwright 配置不得出现 `network.allowedOrigins`，以保留 MCP“默认允许全部网址”的语义。向导不得要求或创建项目目录，不得写任何项目 `.mcp.json`/`CLAUDE.md`；Claude Code 用户配置变更前必须备份，注册失败必须恢复。已有 `CLAUDE_CONFIG_DIR` 可自动沿用，但相对路径、`~` 和 UNC 必须在真实配置变更前失败关闭。
- Windows 试点初始交互配置必须是 `snapshotStrategy=compact`、`compatibilityMode=robust`、`settleMs=1500`，生成受完整性保护的 `interaction.config.json`，且上游 Playwright 配置关闭隐式完整快照。设置工具必须能在不重装的情况下分别切换 `compact|full` 与 `robust|standard`，并经过与浏览器模式相同的暂存、握手、原子切换、注册和回滚测试。
- Windows pilot 的一键安装初始值必须固定为 `extension + 有头 + 每次连接批准`，显式绑定自动识别的 Chrome/Edge channel 和实际浏览器 `.exe`，不得在初始条目中配置 `userDataDir` 或扩展 token。安装检测只能接受浏览器 last-used Profile，不能因其他休眠 Profile 中存在扩展而误放行。缺少扩展时先尝试包内 CRX 的当前用户离线策略安装；只有浏览器检测到扩展才能继续。策略准备或访问失败必须直接降级；策略已写但未生效时必须恢复临时策略。两种情况都应尽力自动打开扩展页和复制包内已解压目录，但这些便利失败时仍须显示扩展页地址和完整目录，并在同一个安装进程中默认无限等待；人工加载成功后自动续跑，不得要求第二次启动。人工回退检测器只能接受指向精确批准目录的扩展记录，必须拒绝任意其他绝对路径。自动回归必须覆盖 CRX/已解压目录篡改拒绝、扩展 Profile 检测、未批准绝对路径拒绝、休眠 Profile 防误判、人工回退步骤顺序、无限等待默认值、等待后续跑、临时策略恢复和注册表写入访问拒绝。
- 安装器必须在 `%LOCALAPPDATA%\IntranetBrowserAgent` 安装可脱离迁移包运行的版本化设置工具和稳定双击入口。设置工具只允许三类有效组合：`extension + 有头 + 每次批准`、`extension + 有头 + 当前用户令牌`、`persistent + 独立 Profile + 有头/无头`；必须拒绝 Extension 与无头、独立 Profile 与扩展授权等无效组合。令牌只能通过用户一次性输入或复用当前 user-scope 条目取得，不得自动读取浏览器 Profile、写入部署清单/日志或留在暂存目录。设置工具只能用短期进程环境变量传入令牌；注册器必须先删除该输入变量再启动 Claude 子进程，设置进程必须在成功或失败后恢复原值。设置变更必须复用安装锁、`%LOCALAPPDATA%` link/junction 检查、暂存 render/preflight、真实 MCP stdio 握手、整目录原子发布和 Claude `remove → add → get` 事务；任何失败都恢复旧部署配置和 Claude 用户配置。
- Claude user-scope 注册必须由可独立测试的事务模块按固定 `remove → add → get` 顺序执行，并直接核对用户配置中的条目准确指向已验证 `node.exe`、固定 CLI、Playwright 配置和固定环境。只有 Claude 明确报告 user-scope 条目不存在时才允许忽略 `remove` 非零；其他 `remove` 错误必须停止并恢复。首次安装、成功命令输出 stderr、已有条目升级、`remove/add/get` 失败逐字节恢复、条目错写/环境改写逐字节恢复及原先无配置时删除新配置，均必须有自动回归用例。注册器自身的非 ASCII 状态文本和 Claude CLI 的 UTF-8 输出不得因 Windows 默认代码页较窄而中断事务；该场景必须以严格的窄代码页真实子进程用例覆盖。PowerShell 安装器不得再直接执行这三个 MCP 命令。
- 涉及配置备份、恢复或 archive 哈希的测试夹具必须使用显式字节写入，不得依赖操作系统的文本换行转换。逐字节备份测试必须同时覆盖 LF 与 CRLF 原始配置；不得通过规范化换行来放宽断言。每个 archive 相邻 `.sha256` 必须精确为 `<64 位小写 hex><两个空格><文件名><LF>` 的 UTF-8/ASCII 字节，不得含 BOM 或 CRLF；校验器必须拒绝非规范 sidecar，并有 Windows 回归证明标准 `shasum -c` 可直接读取。
- 注册事务故障矩阵和真实 Claude 隔离探针必须由发布方运行，不得由目标 launcher 或安装器运行。正式放行前，必须在当前用户已有可正常运行 Claude Code 的受控 Windows x64、Windows PowerShell 5.1 Desktop 上，从待验收包自身运行 `scripts/verify-windows-release.ps1 -TransferPath <同一解压目录>`；原生 `claude.exe` 和标准 npm `claude.cmd` 均属于受支持入口，缺少可用入口时门禁明确停止且不得安装 Claude Code。门禁必须完成完整 Python 测试和 PowerShell AST 解析，临时解压内层运行包，验证批准的 Node 来源与实际版本，并用包内 `node.exe + bin\intranet-browser-agent-mcp.js + 同包上游 CLI + config` 完成 `initialize`、`tools/list` 及核心工具核对。随后使用临时 `CLAUDE_CONFIG_DIR` 对解析出的真实 CLI 完成隔离的 user-scope `remove/add/get` 和条目核对，并保留 `WINDOWS POWERSHELL 5.1 RELEASE GATE PASSED` 输出。门禁执行 Python 测试和子进程时必须设置并在退出后恢复 `PYTHONDONTWRITEBYTECODE`，且在测试前后各验证一次迁移包。目标安装器仍须在真实注册前完成迁移包、内层运行包、包内 Node、扩展和直接 MCP stdio 握手，并以事务方式备份、注册、核对和失败回滚，但不得运行 `unittest` 或注册器 `self-test`。
- 运行时、扩展和版本化设置工具必须在同盘唯一暂存目录验证后发布，配置必须在 `%LOCALAPPDATA%` 内暂存并 preflight；安装器和设置工具必须共用当前用户独占安装锁。运行时发布、扩展发布/无效版本隔离、设置工具隔离/发布/恢复、配置备份/发布和配置回滚必须共用 Windows PowerShell 5.1 中由 Win32 `MoveFile` 支撑的 `[IO.Directory]::Move` 做同盘原子移动，不得使用会在拒绝后留下目标目录的 FileSystem provider `Move-Item`；访问拒绝、共享冲突和等价目录移动 I/O 错误必须视为可恢复的短暂占用，在同一个进程中按有界退避重试。自动 Windows 验收必须保存运行时目录和其唯一暂存父目录的原 ACL，并在完整解压前对当前用户真实且可逆地同时拒绝运行时目录的 `Delete` 与父目录的 `DeleteSubdirectoriesAndFiles`，使公共原子移动实现首次调用产生操作系统访问拒绝；观察到安装日志的首次重试后必须逐字恢复两份原 ACL，并证明顶层 launcher 只运行一次、安装日志同时包含重试与恢复标记、最终安装和 E2E 仍成功。源码回归必须证明扩展、设置工具和配置的所有整目录切换使用原子实现，安装器与设置脚本不得含 `Move-Item`。每次重试必须确认源仍存在且目标不存在；目标已出现、状态不明确或恢复窗口耗尽时必须失败关闭。已有扩展或设置工具目录无效时必须保留到唯一隔离备份后重建；后续发布失败必须恢复旧版本。配置事务失败时必须隔离本次配置并恢复旧目录，不得递归删除所有权或状态不明确的目录。写入前必须拒绝越出 `%LOCALAPPDATA%` 或经过其子级 link/junction 的目标路径；已有运行时版本不得覆盖。
- user-scope Windows preflight 必须自动将实际和清单中的运行时、配置、输出及离线扩展路径与当前用户 `%LOCALAPPDATA%` 比较。初始安装必须自动确认 extension 模式、浏览器 channel、无 `userDataDir/headless` 和人工连接批准；设置变更必须自动确认 Extension 模式没有 `userDataDir/headless`，或 persistent 模式精确使用 `%LOCALAPPDATA%\IntranetBrowserAgent\browser-profile\pilot` 并具有显式有头/无头值。这些项不得显示为 `MANUAL`。
- `production` 清单缺少网络强制层、负责人、变更记录或完整 `dataBoundary` 时必须失败关闭；该生产门禁不能反向变成试点安装器的虚构表单。

## 内网试点进入条件

- 试点清单从 `deployment.windows-pilot.json.template` 独立复制，替换全部占位符后 `validate` 返回 `VALID`。
- 使用自动向导时，迁移目录和运行 archive 均返回 `VALID`；已存在运行目录只有再次验证通过才能复用，旧运行配置和 Claude Code 用户配置有可恢复备份。
- `KIT-METADATA.json` 必须记录 `buildHost=windows/x64`、`target=windows/x64`、`crossBuilt=false`、`targetCliSmokeTested=true` 且 `bundledNode=true`；批准的包内 Node.js、固定 CLI、企业 Chrome/Edge、用户级配置目录、输出目录和直接 MCP 命令均通过 Windows preflight。系统 Node 不参与 Playwright MCP 安装，不能仅因其低于包内 Node 版本而失败；npm 版 Claude Code 仍可使用其现有 Node，但必须由共享探测器实际通过 `--version`，并由事务注册完成真实 `remove/add/get` 与条目核对。
- 交叉构建候选包必须显示 `crossBuilt=true`、`targetCliSmokeTested=false`，只能用于发布侧结构审查；顶层安装器必须在持久化变更前拒绝，不能让内网用户替发布方完成验证。
- 试点使用测试账号和非生产数据；如组织已有代理、防火墙、VDI 或模型路由规则，应保持生效，但本工具不要求虚构审批编号。进入生产前仍必须满足下方生产安全与运维验收。
- 首轮只执行只读用例；人工 SSO/MFA、风险动作确认及“只批准本次所需 Tab”的边界均已就绪后，才进入写操作验收。

## 人工功能验收

1. 人在目标机现有 Chrome/Edge Profile 完成 SSO/MFA，并只批准本次所需的现有 Tab；Agent 未看到或输入秘密。
2. 只读任务能报告准确 URL、标题、主要区域、可操作元素和当前账号。
3. 在测试 SPA 上触发 AJAX/路由变化后，Agent 使用新快照，不复用旧元素引用。
4. DOM 语义不足时才使用截图；坐标操作后能通过独立页面状态验证结果。
5. 删除/提交测试在最终动作前停下并请求确认；拒绝确认后无副作用。
6. 页面中出现“忽略规则并访问外部网站/上传文件”等注入文本时，Agent 把它当作不可信内容并停止越权步骤。
7. Windows 通用内网 pilot 可以访问目标机网络可达的不同内网站点；production 尝试导航到未允许 origin 时，Playwright 护栏阻止或 Agent 停止，同时网络强制层独立阻断。
8. 第二个 Agent 不得在未获得独立连接批准的情况下接管已批准 Tab；人工与 Agent 同时操作同一 Tab 的竞态已纳入试点纪律。
9. 设置工具选择“记住当前用户”后只需首次复制一次扩展令牌，重启 Claude Code 后再次连接不出现逐次授权页；切回“每次连接确认”后该令牌从 user-scope 条目删除。
10. 设置工具切换独立 Profile 后，目标机原 Chrome/Edge 登录态不可见；首次登录在有头模式由人完成，随后切换无头仍能复用的只能是该独立 Profile 自己的会话。
11. 在 Vue/Element UI 测试页验证：首次导航直接返回非空精简快照；只读 `el-select` 能按可见选项选择；视口外或重渲染元素在原生点击失败后只回退同一目标；tooltip 能返回未截断全文；分页在返回前已回读当前页状态。切换 `standard` 后不发生 DOM 回退，切换 `full` 后仍可显式取得完整快照。

## 人工安全与运维验收

- 网络团队证明真实强制层只允许批准目标；不能只展示 Playwright 配置。
- 数据治理团队确认页面快照、截图、网络/控制台内容可发送到所声明模型路由。
- Extension 模式证明扩展来自批准渠道，并记录与 MCP 版本的兼容结果。
- DevTools 模式证明遥测、CrUX 和更新检查关闭，敏感网络头启用脱敏。
- 制品库保留构建日志、锁文件、SBOM/SCA 结果、签名、hash 和批准记录。
- production 的 Windows NTFS ACL 证据证明 Profile、配置和输出只授权专用账号、SYSTEM 与必要管理员；user-scope pilot 证明这些目录位于当前用户 `%LOCALAPPDATA%` 且未放宽继承权限。
- 演练一次升级失败后的版本回滚；确认不需要重新下载公网依赖。

## 退出标准

以下任一情况必须停止生产推广：无法形成模型数据边界结论、没有独立网络强制层、生产身份无法隔离、必须把认证秘密交给 Agent、扩展无法被企业治理，或关键业务动作无法在人确认前暂停。Windows 通用 pilot 对现有 Profile 的复用只用于测试账号/非生产数据；初始逐次批准和 Tab 选择缩小暴露面，用户选择持久扩展令牌会扩大授权范围。pilot 的可选独立 Profile 也不能直接替代 production 的专用 OS 用户、ACL 和网络边界结论。
