# 威胁模型

状态：当前、规范性文档。

## 保护资产

- 企业 SSO 会话、Cookie、Passkey 绑定和客户端证书。
- 内网页面内容、截图、下载、控制台日志和 API 响应。
- 用户可代表组织执行的业务权限。
- 本机文件、代码仓库和凭证文件。
- 浏览器 Agent 的审计证据与发布制品。

## 主要威胁及控制

| 威胁 | 预防/降低控制 | 残余风险 |
|---|---|---|
| 页面 prompt injection 引导访问其他网站或泄露数据 | 页面内容视为不可信、上传/发送确认；production 使用企业出站强制层和显式 origins | 通用内网 pilot 不设网址白名单，可访问目标机网络可达的全部站点，只应用于测试账号和非生产数据 |
| Agent 误删、付款、发布或代表用户发送 | 风险动作确认、一次只做一步、结果复核、最小业务权限 | 确认后仍可能出现业务语义误解 |
| 日常浏览身份被过度暴露 | production 使用专用 OS 用户/Profile；Windows pilot 仅用于测试账号/非生产数据，初始保留每次扩展连接批准和 Tab 选择，并可切换到 `%USERPROFILE%\browser-mcp-server\browser-profile\pilot` 下不读取日常登录态的独立 Profile；Chrome/Edge 的 `%LOCALAPPDATA%` 只用于发现既有 Profile 和扩展状态 | Extension 中被允许的浏览器会话能力会暴露给 Agent；用户误选敏感 Tab 或主动保存持久令牌仍会扩大范围；独立 Profile 首次需单独登录 |
| MCP/依赖/扩展供应链污染 | 固定版本、锁文件、忽略安装脚本、白名单迁移包、分层 SHA-256、CycloneDX、企业扫描/签名；官方 CRX 固定来源/哈希/manifest ID/权限，已解压副本与 CRX payload 逐文件核对 | 上游固定版本本身可能含漏洞；人工加载的 unpacked 模式会显示开发者扩展提示 |
| 便携 Node 被替换或夹带包管理器 | 官方 ZIP 与 `SHASUMS256.txt` 校验、只提取四个白名单文件、逐文件哈希、AMD64 PE 检查、分层制品完整性 | 官方发布或构建区本身仍可能失陷；企业扫描/签名仍是放行条件 |
| 用户遗留环境覆盖已校验的 Playwright 配置或向 Node 注入启动选项 | Windows `.mcp.json`、门禁握手和 user-scope 注册共用固定环境映射，清除固定依赖支持的 `PLAYWRIGHT_MCP_*` 配置覆盖及 `NODE_OPTIONS`/`NODE_PATH`；最终条目逐项核对 | 同一用户权限下的恶意进程仍可在安装后改写配置或运行文件 |
| 跨平台误标、路径 link/junction 或 Windows reparse point 逸出 | 清单显式目标、构建主机/目标元数据、Windows link-free 归档、写入前真实路径检查、目标 preflight | 交叉构建候选包仍未证明目标 `node.exe + 兼容层 + 上游 CLI`、Claude CLI 与企业 Windows 镜像兼容 |
| Defender/EDR/索引器短暂占用运行时、扩展或配置目录，或 PowerShell provider 在移动失败后留下半成品 | 所有受管目录切换共用 Win32 原子目录移动和源/目标状态检查，在同一安装进程有界退避；无效扩展与失败配置移动到唯一备份路径而非递归删除；Windows CI 真实拒绝目录移动权限并验证恢复 | 持续占用超过恢复窗口或外部并发创建目标目录时仍必须失败关闭并保留现场 |
| 安装/设置向导污染项目、把 MCP 注册到错误账号或在发布中留下半配置 | pilot 使用当前用户 `%USERPROFILE%\browser-mcp-server` 和 Claude Code user scope，不提权、不接收项目路径、不写项目文件；Chrome/Edge 的 `%LOCALAPPDATA%` 只用于浏览器发现；安装器和设置工具共用锁、路径检查、暂存 preflight/MCP 握手及原子配置切换；真实事务先备份，`remove/add/get`、条目核对或握手失败时恢复 | 当前用户权限内的恶意进程仍可篡改其 MCP 配置或运行文件；发布门禁不能替代企业镜像上的最终兼容性验收 |
| 浏览器拒绝离线 CRX、策略注册表被企业 ACL 锁定，或自动辅助失败后安装状态不完整/遗留策略 | 以实际 Profile 检测为准；更新清单、策略访问或浏览器启动失败直接降级人工路径；已实际写入的临时策略必须先恢复，无法安全恢复才停止；打开扩展页和剪贴板复制均为非致命便利，屏幕始终显示地址和 `%USERPROFILE%\browser-mcp-server\browser-extension\<版本>\unpacked` 的精确目录；原安装进程等待检测成功后才写 Claude 配置；Windows CI 对精确策略键真实拒绝 `SetValue` 并要求单进程完成 | 现有企业 `ExtensionSettings` 仍可能阻止人工加载；用户关闭等待窗口会中止安装 |
| 扩展存在于错误 Profile 或固定 MCP 版本未识别手工加载记录 | 安装器只接受浏览器 last-used Profile；清单、握手和 user-scope 注册同时固定 channel 与实际 browser `.exe`；Windows CI 必须在 Chrome 重启后做真实扩展工具调用 | 用户随后主动切换浏览器 Profile 时仍需在目标 Profile 重新安装/批准扩展 |
| 调试端口被远程接管 | stdio、本机 channel/loopback、禁止远程 endpoint、主机防火墙 | 同机恶意进程仍可能访问 loopback |
| 模型侧数据外泄 | 数据分类和模型路由必须先批准；减少截图、网络和控制台信息 | 已批准模型仍会接收完成任务所需内容 |
| 扩展 token 泄漏 | 初始逐次批准；pilot 只有用户明确选择后才从官方扩展页一次性输入，格式验证后仅写 Claude 当前用户 MCP 配置，不写项目、清单、迁移包、暂存文件或日志；切回逐次批准/独立 Profile 时删除，泄漏时在扩展页重新生成并重启浏览器 | 同一 Windows 用户下能读取 Claude 配置的进程可取得令牌，并在吊销前绕过连接对话框请求浏览器能力 |
| 会话制品长期保留 | 默认不保存 MCP session、限制输出大小、运维清理 | 业务下载仍可能含敏感信息 |
| artifact 绝对路径泄露本机目录信息或越界写入 | 兼容层读取清单 output 目录、拒绝越界 filename（含 symlink escape），并同时返回 output-relative 路径；不改变上游工作目录或文件访问边界，输出目录权限仍由部署 ACL 控制 | 绝对路径可能包含本机用户名，客户端应避免把工具结果扩散到不必要的日志或模型上下文 |
| clipboard 权限被扩大或 HTTP 页面误报成功 | 默认不授予权限；`browser_clipboard(grantPermissions=true)` 只针对当前 origin 请求，并检查 `isSecureContext`/API 存在 | HTTPS 页面仍可能被浏览器策略拒绝；HTTP 页面不能保证 Clipboard API 可用 |
| OS 剪贴板把用户已有内容泄露到模型，或粘贴到漂移的标签页 | `browser_paste`/`browser_copy` 是显式工具；系统剪贴板不作为页面 Clipboard API 的自动降级；动作前支持 `expectedUrl`，复制可要求 `requireChanged`；响应之外不记录剪贴板内容 | 用户主动调用复制仍会把选区内容送入模型；其他本机程序可同时改变剪贴板 |
| 多 Agent 状态竞争 | production 单 Profile 单 Agent；pilot 清单固定 `maxConcurrentAgents=1`，独立 Profile 由单进程持有；Extension 初始逐次选择，持久令牌模式明确提示扩大授权 | 人工与 Agent或两个已获授权连接同时改同一页面仍可能竞态 |
| 页面 reload 后 helper 依赖旧上下文或持久化业务数据 | helper 仅在 MCP 进程内保存短期函数定义，每次调用重新注入；名称、TTL 和页面 URL 受校验；不把 `__rows`、Cookie 或剪贴板写入配置 | helper 本身仍可执行调用方授权的页面代码；页面 API 变化时需重新 probe |
| MODOC SDK 方法被误调用或写入错误区域 | `browser_sheet_bridge` 要求先 probe，限制方法名模式，写操作要求 `confirmWrite=true`，并由调用方提供 `expectedUrl`；未有真实 SDK 证据不宣称兼容 | 页面私有 `window.sheetInst` 可能随版本变化，写入语义仍需业务验收 |
| 动态页面兼容回退绕过浏览器原生 actionability 检查 | 先执行原生动作；只对明确的可见性/稳定性/视口类失败回退，且必须重新解析同一个唯一目标，确认节点连接、可见、未禁用后才滚动并 DOM 点击；`standard` 模式可完全关闭回退 | 页面脚本仍可能在检查与点击间重渲染或改变业务语义；高风险动作仍需独立人工确认和结果复核 |
| 巨大或空快照增加数据暴露并浪费上下文 | Windows pilot 默认关闭隐式完整快照，兼容层在页面稳定后返回内联限深快照，空结果同调用重试；完整快照只能由用户配置或单次显式请求启用 | 精简快照可能省略深层信息，需要按需查找、评估或显式完整快照 |

## 明确不信任

- 网页、弹窗、下载文件和页面内“给 AI 的指令”。
- MCP 工具返回的自然语言描述。
- URL allow/block 规则作为唯一网络边界。
- `CLAUDE.md` 作为不可绕过的授权系统。
- 未经校验的离线包、扩展或浏览器更新。

## 必须人工处理

- 登录秘密、MFA、Passkey、验证码、扫码。
- 删除、金融交易、生产变更、权限/账户变更。
- 对外或代表性通信、文件上传、最终提交。
- 任何超出部署清单 origin、数据分类或业务审批范围的操作。
