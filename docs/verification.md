# 本次验证记录

状态：2026-08-30 的验证证据快照，不是规范性设计文档。

发布结论：**SELF-GATING CANDIDATE 1.0.8**。它是供一次 Windows x64 目标兼容性验证使用的自检候选，不是已经过 Windows 验收的正式制品，更不是生产制品。

1.0.6 已撤回：其真实 Windows PowerShell 5.1 门禁在字节保真测试中暴露 CRLF 夹具缺陷。1.0.7 也在交付前撤回：复核发现把 `.cmd` 作为实际 MCP 命令、Node 来源只做自洽校验，以及运行时/配置发布事务不够严格。1.0.8 才包含直接 `node.exe + cli.js`、固定 Node 批准哈希、固定 MCP 子进程环境、真实 MCP stdio 握手和本记录所述的事务收紧。

## 已执行并通过

- 源码测试：本机构建环境执行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`，64/64 通过。覆盖 Windows 路径、user scope、版本一致性、LF/CRLF 逐字节备份、`remove/add/get` 注册失败回滚、条目/环境错写、窄 Windows 代码页与 Claude UTF-8 输出组合、遗留环境清理、Node 来源、archive 路径、配置漂移、发布顺序和 MCP stdio 握手。
- 语法与格式：全部 Python 文件通过 `compileall`；shell 入口通过 `bash -n`；11 个 JSON/JSON template 通过标准 JSON 解析；`git diff --check` 通过。
- PowerShell 解析：`build-offline-bundle.ps1`、`INSTALL-WINDOWS-PILOT.ps1`、`verify-windows-release.ps1` 均由官方 PowerShell 7.6.5 parser 返回无 AST 错误。该证据不能替代 Windows PowerShell 5.1；包内门禁会在目标机用 5.1 parser 再解析全部 PowerShell 脚本。
- 清单与渲染：本地 demo 返回 `VALID`。Windows pilot 模板固定 `mcpScope=user`、`workspaceRoots=[]`，省略 `network`/`dataBoundary`，只因明确占位符而失败关闭；production 模板继续因未批准字段和占位符失败关闭。Windows `.mcp.json` 的命令直接指向包内 `node.exe`，首个参数指向固定 Playwright CLI，不使用 `.cmd` shell shim；环境与 `config/windows-mcp-environment.json` 完全一致且不含扩展 token。
- 注册事务：`register_claude_user_mcp.py self-test` 作为真实子进程通过。自动用例证明 user-scope `remove → add → 实际用户配置核对 → get` 顺序、首次安装明确缺少旧条目继续、其他 `remove` 错误停止、成功 stderr 非致命、已有配置 LF/CRLF 逐字节备份、`remove/add/get` 失败恢复、条目或环境错写时恢复，以及原配置不存在时删除新配置。另以 `PYTHONIOENCODING=cp1252:strict` 启动真实注册入口，并让假 Claude 直接输出 UTF-8 中文 stderr；事务仍成功，证明注册状态文本不会再因窄代码页产生 `UnicodeEncodeError`。
- MCP 协议：`smoke_playwright_mcp.py` 直接启动 Node + 固定 CLI + 生成的 Windows pilot Playwright 配置，使用与最终注册相同的固定环境并发送 MCP `initialize`、`notifications/initialized` 和 `tools/list`。恶意测试值 `PLAYWRIGHT_MCP_CONFIG`/`NODE_OPTIONS` 会被清空。构建主机实际返回 Playwright server `1.63.0-alpha-2026-08-05`、30 个唯一工具，并包含 `browser_navigate`、`browser_snapshot`。这证明固定 JavaScript 运行时与渲染配置可完成 stdio 协议握手，但不是 Windows `node.exe` 的执行证据。
- 固定依赖构建：使用 Node.js v24.19.0、pnpm 11.19.0、Python 3.12.13，依据冻结锁文件安装生产依赖，禁用安装脚本和 Playwright 浏览器下载。core 运行时只保留 `@playwright/mcp`、`playwright`、`playwright-core`，不含 Chrome DevTools MCP、pnpm 元数据或平台原生依赖。
- Node 来源：从 Node.js v24.19.0 官方 Windows x64 ZIP 与同版本 `SHASUMS256.txt` 生成四文件最小 distribution。实测 ZIP SHA-256 为 `57f71ab3652e797d84acddc79c81cc9ff1c6ddb2a1974cdb83f00fee9bff4c73`，`SHASUMS256.txt` 为 `be0629ee2bcd8e40bb856abdd3407f0762101b76bd60a36b8867f637733631c0`，`node.exe` 为 `3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237`，`LICENSE` 为 `d9c4eeda951d6d08f4aa1316b61aafcf67e6da5f79b18f8edeb56fa6abdc038c`；均与 `config/windows-node-sources.json` 一致。自洽但未批准的伪造来源回归会失败。
- 运行包：构建了 `browser-agent-runtime-1.0.8-core-windows-x64.tar.gz`。其构建元数据明确记录 `buildHost=darwin/arm64`、`target=windows/x64`、`crossBuilt=true`、`targetCliSmokeTested=false`、`bundledNode=true`、Node v24.19.0、pnpm 11.19.0；不会冒充 Windows 原生构建。archive 与解压目录均返回 `VALID`，按构建契约对 `node_modules` 的可移植性扫描返回 `PORTABLE`；单独受控的最小 Node 返回 `VALID NODE DISTRIBUTION`。
- 运行包结构：239 个 archive member，0 symlink、0 hardlink、0 device/FIFO，最长 archive 路径 146 字符；`SYMLINKS.json` 为空，`node_modules` 中无 `.exe/.dll/.node/.so/.dylib`，唯一目标 EXE 是批准的 `node/node.exe`，Node 目录精确包含 `LICENSE`、`SOURCE.json`、`VERSION`、`node.exe`。安装器使用 `%LOCALAPPDATA%\IntranetBrowserAgent\staging\r-*` 短暂存路径，降低传统 Windows 路径长度风险。
- archive 防护：校验器拒绝绝对路径、`..`、反斜杠、ADS 冒号、Windows 保留名、尾随空格/点、大小写碰撞、hardlink/device/FIFO、越界 symlink，以及用 symlink 冒充 `SHA256SUMS`/`SYMLINKS.json`。运行包和迁移包均无链接。
- 安装事务：运行时先在同盘短目录解压，完整性、Node 批准哈希和实际版本通过后才发布固定版本目录。配置先在 `%LOCALAPPDATA%` 暂存并 preflight，再整目录切换；“旧目录备份完成”和“新目录发布完成”独立记账，备份动作本身失败时不会删除原目录。后续失败恢复旧配置和 Claude 用户配置。
- Windows 门禁静态/单元证据：顶层 `.cmd` 只需双击一次，先规范化无尾随分隔符的迁移目录，门禁失败不调用安装器并直接显示日志尾部。门禁拒绝 archive 参数和来自另一目录的脚本，只验证自身所在的同一解压迁移包；在测试前后各校验一次，禁写 Python bytecode，解析全部 PowerShell 脚本，运行完整测试和假 Claude 自检，临时解压内层运行时，校验目标 Node，使用固定环境执行真实 MCP stdio 握手，再在临时 `CLAUDE_CONFIG_DIR` 用原生 Claude CLI 做隔离注册探针。安装阶段会先对最终安装路径 preflight，再用生成配置执行同一 MCP 握手。
- 迁移包：逐文件白名单包含 61 个源码/文档文件，不收录 `.git`、真实部署清单、`build/`、`dist/`、缓存或 Python bytecode。最终迁移 archive、解压迁移目录、包内运行 archive、解压运行目录四层均返回 `VALID`；包内 63 项测试、注册 self-test 和 MCP 握手脚本随包交付。最终重建后的外层 SHA-256 以相邻 `.sha256` 和交付回复为准。

## 已有 Windows 事实

1.0.6 曾在真实 Windows x64、Windows PowerShell 5.1 上由顶层 launcher 进入自动门禁并运行 44 项测试：43 项通过，1 项失败。失败原因是测试夹具用文本 API 写 LF 后被 Windows 转为 CRLF，而断言硬编码 LF；门禁按设计在安装前停止，未修改真实 Claude 配置。修复后夹具改用显式字节写入，并同时覆盖 LF 与 CRLF，没有通过换行归一化放宽断言。

这条历史事实只证明旧门禁能在该目标启动并正确失败关闭，不等于 1.0.8 已通过 Windows。

## 当前验证环境

- 源码测试主机：macOS arm64，Python 3.14.6。
- 运行包构建工具：Node.js v24.19.0、pnpm 11.19.0、Python 3.12.13。
- PowerShell 解析器：官方 PowerShell 7.6.5 for macOS arm64。
- 当前主机无 Windows，也未发现可执行的 Claude Code CLI。
- 候选目标：Windows x64；包内 Node.js 为批准的官方 Windows x64 v24.19.0 最小 distribution。

## 尚未验证，不能声称完成

- 1.0.8 在真实 Windows x64、Windows PowerShell 5.1 上完整输出 `WINDOWS POWERSHELL 5.1 RELEASE GATE PASSED`。
- 目标机实际执行包内 `node.exe --version`、用该 EXE 完成 MCP stdio `initialize/tools/list`、原生 `claude.exe mcp add/get --scope user` 隔离探针，以及安装后的真实 user-scope 注册。
- `%LOCALAPPDATA%`、NTFS、EDR/杀毒、企业 PowerShell 策略和实际用户配置并发行为；安装失败/升级失败的目标机目录与 Claude 配置回滚。
- Chrome/Edge 实际启动、专用 Profile、企业证书、PAC/代理、SSO/MFA 与业务页面读写行为。
- 企业 SCA、恶意代码扫描、制品签名、制品库导入及生产审批证据。

因此，下一次目标机只运行顶层 `INSTALL-WINDOWS-PILOT.cmd` 一次。该单次流程会先完成上述 Windows 门禁；门禁不过则安装不开始，门禁通过后同一窗口继续安装。生产制品仍必须在受控 Windows x64 构建机原生重建，并完成 `docs/acceptance.md` 的生产验收项。
