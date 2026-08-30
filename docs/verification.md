# 本次验证记录

状态：2026-08-30 的验证证据快照，不是规范性设计文档。

## 1.0.9 候选状态（尚未 Windows 放行）

1.0.9 改为 Windows pilot `extension` 模式，迁移包携带固定官方 CRX 和逐文件一致的已解压副本；安装器先尝试完全离线策略安装，失败时恢复临时策略、打开扩展页并在原进程等待用户加载，检测成功后继续。当前本地完整测试、真实 CRX 校验、交叉构建及 macOS Chrome 临时 Profile 的 `Extensions.loadUnpacked` 实测已通过；后者实际返回扩展 ID `mmlmfjhmonkocbjadbfplnigmagldckm`、版本 `0.3.0`、`enabled=true`，关闭 Chrome 后 Profile 检测仍返回 `Default`。这些证据尚不能替代 Windows PowerShell 5.1、Windows Chrome 和顶层 launcher 的 GitHub 实测；在对应 workflow 成功前，1.0.9 仍是候选，不得交付为 Windows 已验证包。

发布结论：**WINDOWS CI-VERIFIED PILOT 1.0.8**。GitHub 托管的 Windows x64 环境已经完成源码测试、Windows 原生重建、PowerShell 5.1 发布门禁、真实 Claude Code 隔离注册和一次顶层 launcher 安装。该结论证明通用 Windows 自动链路可用，但不等于已通过企业内网、业务浏览器或生产治理验收。

1.0.6 已撤回：其真实 Windows PowerShell 5.1 门禁在字节保真测试中暴露 CRLF 夹具缺陷。1.0.7 也在交付前撤回：复核发现把 `.cmd` 作为实际 MCP 命令、Node 来源只做自洽校验，以及运行时/配置发布事务不够严格。1.0.8 才包含直接 `node.exe + cli.js`、固定 Node 批准哈希、固定 MCP 子进程环境、真实 MCP stdio 握手和本记录所述的事务收紧。

## 已执行并通过

- 源码测试：本机构建环境执行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`，64/64 通过。覆盖 Windows 路径、user scope、版本一致性、LF/CRLF 逐字节备份、`remove/add/get` 注册失败回滚、条目/环境错写、窄 Windows 代码页与 Claude UTF-8 输出组合、遗留环境清理、Node 来源、archive 路径、配置漂移、发布顺序和 MCP stdio 握手。
- 语法与格式：全部 Python 文件通过 `compileall`；shell 入口通过 `bash -n`；11 个 JSON/JSON template 通过标准 JSON 解析；`git diff --check` 通过。
- PowerShell 解析：`build-offline-bundle.ps1`、`INSTALL-WINDOWS-PILOT.ps1`、`verify-windows-release.ps1` 均由官方 PowerShell 7.6.5 parser 返回无 AST 错误；GitHub Actions 的 `windows-2022`、`windows-latest` 两个 job 又分别使用 Windows PowerShell 5.1 Desktop 解析仓库内全部 PowerShell 脚本并通过。
- 清单与渲染：本地 demo 返回 `VALID`。Windows pilot 模板固定 `mcpScope=user`、`workspaceRoots=[]`，省略 `network`/`dataBoundary`，只因明确占位符而失败关闭；production 模板继续因未批准字段和占位符失败关闭。Windows `.mcp.json` 的命令直接指向包内 `node.exe`，首个参数指向固定 Playwright CLI，不使用 `.cmd` shell shim；环境与 `config/windows-mcp-environment.json` 完全一致且不含扩展 token。
- 注册事务：`register_claude_user_mcp.py self-test` 作为真实子进程通过。自动用例证明 user-scope `remove → add → 实际用户配置核对 → get` 顺序、首次安装明确缺少旧条目继续、其他 `remove` 错误停止、成功 stderr 非致命、已有配置 LF/CRLF 逐字节备份、`remove/add/get` 失败恢复、条目或环境错写时恢复，以及原配置不存在时删除新配置。另以 `PYTHONIOENCODING=cp1252:strict` 启动真实注册入口，并让假 Claude 直接输出 UTF-8 中文 stderr；事务仍成功，证明注册状态文本不会再因窄代码页产生 `UnicodeEncodeError`。
- MCP 协议：`smoke_playwright_mcp.py` 直接启动 Node + 固定 CLI + 生成的 Windows pilot Playwright 配置，使用与最终注册相同的固定环境并发送 MCP `initialize`、`notifications/initialized` 和 `tools/list`。恶意测试值 `PLAYWRIGHT_MCP_CONFIG`/`NODE_OPTIONS` 会被清空。本机构建返回 Playwright server `1.63.0-alpha-2026-08-05`、30 个唯一工具；Windows 原生包内的 `node.exe` 在发布门禁中返回同一 server 版本、24 个唯一工具。两端均包含并核对 `browser_navigate`、`browser_snapshot` 等核心工具，Windows 结果是目标 EXE 的真实 stdio 执行证据。
- 固定依赖构建：使用 Node.js v24.19.0、pnpm 11.19.0、Python 3.12.13，依据冻结锁文件安装生产依赖，禁用安装脚本和 Playwright 浏览器下载。core 运行时只保留 `@playwright/mcp`、`playwright`、`playwright-core`，不含 Chrome DevTools MCP、pnpm 元数据或平台原生依赖。
- Node 来源：从 Node.js v24.19.0 官方 Windows x64 ZIP 与同版本 `SHASUMS256.txt` 生成四文件最小 distribution。实测 ZIP SHA-256 为 `57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73`，`SHASUMS256.txt` 为 `be0629ee2bcd8e40bb856abdd3407f0762101b76bd60a36b8867f637733631c0`，`node.exe` 为 `3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237`，`LICENSE` 为 `d9c4eeda951d6d08f4aa1316b61aafcf67e6da5f79b18f8edeb56fa6abdc038c`；均与 `config/windows-node-sources.json` 一致。自洽但未批准的伪造来源回归会失败。
- 运行包：本地交叉构建的 `browser-agent-runtime-1.0.8-core-windows-x64.tar.gz` 明确记录 `buildHost=darwin/arm64`、`target=windows/x64`、`crossBuilt=true`、`targetCliSmokeTested=false`，不会冒充 Windows 原生构建；其 archive/解压目录、可移植性和最小 Node 校验均通过。GitHub Actions 另在 Windows x64 上使用固定 Node v24.19.0、pnpm 11.19.0 原生重建同版本运行包，并以包内 `node.exe` 完成版本与 MCP 握手后才组装迁移包。
- 运行包结构：239 个 archive member，0 symlink、0 hardlink、0 device/FIFO，最长 archive 路径 146 字符；`SYMLINKS.json` 为空，`node_modules` 中无 `.exe/.dll/.node/.so/.dylib`，唯一目标 EXE 是批准的 `node/node.exe`，Node 目录精确包含 `LICENSE`、`SOURCE.json`、`VERSION`、`node.exe`。安装器使用 `%LOCALAPPDATA%\IntranetBrowserAgent\staging\r-*` 短暂存路径，降低传统 Windows 路径长度风险。
- archive 防护：校验器拒绝绝对路径、`..`、反斜杠、ADS 冒号、Windows 保留名、尾随空格/点、大小写碰撞、hardlink/device/FIFO、越界 symlink，以及用 symlink 冒充 `SHA256SUMS`/`SYMLINKS.json`。运行包和迁移包均无链接。
- 安装事务：运行时先在同盘短目录解压，完整性、Node 批准哈希和实际版本通过后才发布固定版本目录。配置先在 `%LOCALAPPDATA%` 暂存并 preflight，再整目录切换；“旧目录备份完成”和“新目录发布完成”独立记账，备份动作本身失败时不会删除原目录。后续失败恢复旧配置和 Claude 用户配置。
- Windows 门禁：顶层 `.cmd` 只需启动一次，先规范化迁移目录；门禁拒绝 archive 参数和来自另一目录的脚本，只验证自身所在的同一解压迁移包。GitHub Windows 原生 job 已实际执行测试前后双重完整性校验、64 项测试、全部 PowerShell 5.1 AST 解析、假 Claude 回滚自检、内层运行时临时解压、批准 Node 校验、真实 MCP stdio 握手，以及临时 `CLAUDE_CONFIG_DIR` 下原生 Claude Code 2.1.84 的 `remove/add/get` 隔离探针，并保留 `WINDOWS POWERSHELL 5.1 RELEASE GATE PASSED`。随后同一个 launcher 自动进入安装，安装后运行时、清单和 user-scope 配置再次核对通过。
- 迁移包：逐文件白名单包含 61 个源码/文档文件，不收录 `.git`、真实部署清单、`build/`、`dist/`、缓存或 Python bytecode。迁移 archive、解压迁移目录、包内运行 archive、解压运行目录四层均返回 `VALID`；包内 64 项测试、注册 self-test 和 MCP 握手脚本随包交付。最终重建后的外层 SHA-256 以相邻 `.sha256` 为准。

## Windows Actions 实测事实

- 私有仓库的 [Windows release validation #4](https://github.com/baitianxia/intranet-browser-agent/actions/runs/33299630904) 在提交 `98bdf62ee5c7b6e1f7a4866c0e146958e0f45680` 上成功，总耗时 3 分 25 秒。`windows-2022`、`windows-latest` 两套 PowerShell 5.1 源码 job 与 Windows 原生打包/门禁/安装 job 全部通过。
- 原生 job 安装并核对 Claude Code 2.1.84，使用批准的 Windows Node.js v24.19.0 最小 distribution 和 pnpm 11.19.0 构建。顶层 `INSTALL-WINDOWS-PILOT.cmd` 只运行一次，先通过发布门禁，再在同一进程链完成当前用户安装；后置步骤逐项确认门禁 PASS、安装 SUCCESS、安装摘要、`mcpScope=user`、空 `workspaceRoots`、无 `network` 白名单、真实 Claude user 配置和安装后运行时完整性。
- 该 run 生成 `intranet-browser-agent-transfer-1.0.8-windows-x64-33299630904`（36.8 MB，GitHub artifact SHA-256 `ebfd88b994af37207f1deff0bb16fd599ab73c919daedb9cafb002076bbb2afb`）和 `windows-validation-evidence-33299630904`（GitHub artifact SHA-256 `ac78e65985268b23c4dd3cedd0d0cb4990e0e110c668fe08ac2182edb41f9579`）。证据包含门禁日志、安装日志、安装摘要、实际清单和迁移包 `.sha256`。
- 在最终成功前，Actions 没有掩盖失败：run #1 暴露 NTFS ADS 测试夹具不可移植，run #2 暴露安装 Claude 前错误禁用更新，run #3 暴露 `cp1252` 无法输出中文状态文本。三项均在自动门禁/构建阶段定位并修复；其中注册事务失败时恢复临时 Claude 配置，安装没有被错误放行。
- 1.0.6 的早期人工 Windows 门禁曾暴露 CRLF 夹具缺陷；修复后的逐字节 LF/CRLF 用例现在同时在两套 Windows runner 通过。

## 当前验证环境

- 源码测试主机：macOS arm64，Python 3.14.6。
- 运行包构建工具：Node.js v24.19.0、pnpm 11.19.0、Python 3.12.13。
- PowerShell 解析器：官方 PowerShell 7.6.5 for macOS arm64。
- GitHub CI：`windows-2022` 与 `windows-latest` x64、Windows PowerShell 5.1 Desktop、Python 3.12；原生包 job 另使用 Node.js v24.19.0、pnpm 11.19.0 和 Claude Code 2.1.84。
- 包内 Node.js 为批准的官方 Windows x64 v24.19.0 最小 distribution；内网目标仍为 Windows x64 当前用户安装。

## 尚未验证，不能声称完成

- 企业内网终端上的 EDR/杀毒、组策略、应用白名单、代理/PAC、企业证书和现有 Claude 用户配置并发行为；GitHub 托管 runner 无法替代这些组织特定条件。
- 已有旧版本的真实内网终端升级、外部并发写入和故障注入回滚；这些路径已有自动事务测试，但尚未在企业端点进行破坏性演练。
- Chrome/Edge 实际启动、专用 Profile、企业证书、PAC/代理、SSO/MFA 与业务页面读写行为。
- 企业 SCA、恶意代码扫描、制品签名、制品库导入及生产审批证据。

因此，内网测试只需运行顶层 `INSTALL-WINDOWS-PILOT.cmd` 一次；不再需要用内网机器替代通用 Windows 兼容性测试。该单次流程仍会先在本机完成同一门禁，以捕获组织环境差异；门禁不过则安装不开始，门禁通过后同一窗口继续安装。生产放行仍需完成 `docs/acceptance.md` 的企业扫描、签名、浏览器/SSO 和生产治理项。
