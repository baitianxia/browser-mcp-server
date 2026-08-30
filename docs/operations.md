# 部署与运维

状态：当前、规范性文档。

只做 Windows x64 内网试点时，拿到已校验并解压的目录后按 `windows-quickstart.md` 双击一次安装。本文件保留生产构建、权限、升级、回滚和事件处理细节，不要求试点人员从头通读。

## 1. 有网构建区

1. 使用隔离构建环境和固定的 Node.js 20.19+、pnpm 11.19.0、Python 3.10+。
2. 检查 `runtime/package.json` 与 `runtime/pnpm-lock.yaml` 的变更评审记录。
3. Windows 生产制品必须在 Windows x64 构建机运行 `powershell.exe -NoProfile -File .\scripts\build-offline-bundle.ps1 -Profile core -NodeDistribution <批准目录> -OutputDir .\dist`。执行策略必须由企业策略批准，不使用临时 bypass。macOS/Linux 仅为内网试点交叉组装 Windows 候选包时，运行 `scripts/build-offline-bundle.sh --profile core --target windows-x64 --node-distribution <批准目录>`。
4. 从 `config/playwright-extension-source.json` 记录的 URL 下载精确官方 CRX，用 `validate_playwright_extension.py` 校验批准元数据；再用 `scripts/verify-bundle.py` 校验运行包，并执行 `scripts/build-transfer-kit.py --runtime-archive <runtime.tar.gz> --extension-crx <approved.crx>` 组装完整迁移包。构建器安全提取 CRX payload 并逐文件反向核对，迁移包同时包含 CRX、可供离线人工加载的已解压副本、运行包、校验器、模板、Schema、部署工具、测试和当前文档。
5. 将迁移包、相邻 `.sha256` 和包内 CycloneDX 清单送入企业 SCA/恶意代码扫描和签名流程。
6. 只有扫描、签名和变更审批都通过的制品才能进入内网制品库。

GitHub 仓库的 `.github/workflows/windows-release.yml` 是提交级 Windows 兼容性回归：先在 `windows-2022` 与 `windows-latest` 的 Windows PowerShell 5.1 上跑完整测试和脚本解析，再在 `windows-latest` 原生构建 Windows x64 包，安装固定 Claude Code 2.1.84，并对解压包执行顶层一次点击 launcher、发布门禁、隔离 user-scope 注册、完整安装和安装后完整性核对。运行 launcher 时为 Chrome 添加临时出站阻断；非受管 runner 进入人工扩展回退后，CI 先关闭向导打开的临时 `chrome://extensions` 窗口以释放隔离 Profile，再由专用脚本通过 Chrome 官方 remote-debugging-pipe 把屏幕所指的同一本地目录投递给扩展管理页自身的目录拖放处理器。这条浏览器内用户路径会写入持久 Profile；它不使用只对当前 Chrome 会话有效的 `Extensions.loadUnpacked`，也不声称 GitHub 托管 runner 能替代真人操作原生目录选择框。探针核对启用状态、固定 ID/版本以及关闭 Chrome 后的持久 Profile 记录，让等待中的原安装器自动续跑。安装完成后，出站阻断仍保持，CI 使用实际安装的包内 Node/MCP 和清单中固定的实际浏览器 `.exe` 经扩展访问本机离线页面，要求 `browser_navigate`、`browser_snapshot` 和预置 Profile 会话 Cookie 同时成功，才证明安装结果能复用浏览器登录态。为替代 CI 中不存在的人手，探针只在临时 Profile 写入一次性连接令牌并在步骤结束删除令牌文件；令牌不进入目标迁移包、安装配置、日志或证据，探针也不在内网安装器打开调试端口。成功 run 上传 Windows 原生迁移包及日志证据，失败 run 只保留可获得的诊断证据。该流程使用 GitHub 的临时有网 VM，不能替代企业 SCA、签名、终端策略、SSO/MFA、业务页面或生产审批验收。

默认构建跳过 npm 安装脚本和 Playwright 浏览器下载。Windows 一键包强制通过 `--node-distribution`/`-NodeDistribution` 携带组织批准的最小目标运行时；该目录必须由 `prepare_windows_node_distribution.py` 从官方 Windows x64 ZIP 与同版本 `SHASUMS256.txt` 生成，并通过 `validate_node_distribution.py --approval-file config/windows-node-sources.json` 的四文件白名单、固定批准哈希、版本和 AMD64 PE 校验。禁止把构建主机 Node 或官方 ZIP 中的 npm、npx、corepack 一并带入。目标机不探测、不依赖系统 Node。

GitHub Actions 仅在上述有网构建 VM 中使用 npm 安装精确的 pnpm 11.19.0，以执行冻结锁文件构建；这不是目标机修复步骤。内网 launcher、安装器和运行时都不会运行或携带 npm、pnpm、npx 或 corepack。

迁移包采用白名单收录；不会带入仓库外清单、`deployment.production.json`、`.git`、`build/`、`dist/`、pnpm 缓存或 Python bytecode。真实环境清单应在内网从模板复制并独立保管。

## 2. 内网导入

Windows 试点优先使用迁移包顶层 `INSTALL-WINDOWS-PILOT.cmd`。一次双击先自动运行 Windows PowerShell 5.1 门禁；门禁只接受并校验自身所在的同一解压目录，临时解压内层运行时，实际执行包内 `node.exe --version` 和无浏览器 MCP stdio `initialize + tools/list` 握手，并使用临时 `CLAUDE_CONFIG_DIR` 运行真实 Claude CLI 隔离探针。只有测试前后双重制品校验、完整测试、全部 PowerShell 语法解析、回滚自检、目标 Node/MCP 握手和真实 Claude 探针全部通过，才在同一个窗口开始安装。安装阶段在当前用户权限下检查原生 `claude.exe`、平台和 Python，验证迁移目录、运行包、官方 CRX 及其已解压副本；包内 Node 会按固定批准哈希、来源、文件哈希、AMD64 PE 和实际版本再次验证，系统 Node 不参与。随后按 Chrome 优先、Edge 回退自动识别浏览器，将运行时先解压到 `%LOCALAPPDATA%\IntranetBrowserAgent\staging\r-*` 的短路径同盘暂存目录，校验后发布到版本目录。缺少扩展时先尝试本地 CRX 用户策略；30 秒内浏览器未确认安装，就恢复临时策略、打开扩展管理页、把 `%LOCALAPPDATA%` 下的已解压扩展目录复制到剪贴板并显示三步指引。用户加载后原进程自动检测并继续；默认持续等待，不要求重新运行。配置也先在该根目录内暂存/preflight，再整目录切换。Claude Code 注册由 Python 事务模块执行：先备份用户配置，按固定 user-scope `remove → add → get` 流程注册 `intranet-browser-agent`，并直接核对真实 user-scope 条目指向包内 `node.exe + cli.js` 且环境与 `config/windows-mcp-environment.json` 完全一致；任一步失败就恢复 Claude 用户配置和旧部署配置。固定环境会清除遗留 Playwright MCP 配置覆盖及 Node 注入变量，不需要用户填写。该 MCP 对当前用户所有未被同名更高优先级配置覆盖的项目生效；向导不请求 UAC、不接收项目路径、不写项目文件。

试点不生成 origin 白名单，允许浏览目标机网络当前可达的全部网址；向导不询问或修改防火墙，也不收集生产治理字段。它不修改系统 Node、不运行 npm/pnpm/npx、不绕过执行策略、不下载依赖、不自动完成 SSO/MFA。

自动门禁输出会写入 `%TEMP%\IntranetBrowserAgent\WINDOWS-RELEASE-GATE-*.log`；安装阶段的 Python、完整性验证、Claude Code CLI 和 preflight 输出会写入 `%TEMP%\IntranetBrowserAgent\INSTALL-WINDOWS-PILOT-*.log`。失败时外层 launcher 会显示对应日志路径，首次运行即可保留完整证据，不要求为采集日志重跑。故障报告必须包含 `FAILED`、紧邻的 `PYTHON`/`NATIVE` 行和 stack 行，不能只报告 exit code。

首次安装没有旧的 `intranet-browser-agent` 条目时，只有 Claude 明确返回“user-scope 条目不存在”，注册模块才会继续 `mcp add`；权限、配置解析等其他 `remove` 错误会立即恢复并停止。成功命令即使写入 stderr，也只按退出码判断。注册模块按 UTF-8 容错读取 Claude 输出，并在 Windows 默认代码页无法表示中文状态文本时转义该文本而不中断注册。安装器在操作真实用户配置前，会先在临时目录使用假 Claude CLI 自动演练首次安装、升级、条目/环境错写和 `remove/add/get` 失败回滚；自动测试还会在严格 `cp1252` 输出和 Claude UTF-8 stderr 组合下运行真实注册子进程。写入前检查与 preflight 会自动拒绝 `%LOCALAPPDATA%` 外路径及其根以下的 link/junction，确认清单中的直接 Node 命令、固定 CLI、固定环境、extension 模式、浏览器 channel 和人工连接批准与部署配置匹配。若用户已设置 `CLAUDE_CONFIG_DIR`，向导自动沿用，但只接受本机盘符绝对路径；相对路径、`~` 或 UNC 会在修改配置前失败关闭。

正式放行前，发布人员必须在受控 Windows x64、Windows PowerShell 5.1 Desktop 上执行：

```powershell
powershell.exe -NoProfile -File .\toolkit\scripts\verify-windows-release.ps1 `
  -TransferPath .
```

该命令必须在待验收迁移包的解压顶层运行，脚本会拒绝 archive 路径或来自另一目录的门禁脚本。门禁机器必须安装与试点兼容的原生 Claude Code `claude.exe`。门禁会验证并临时解压内层运行包，执行包内 Node 版本和 MCP stdio 握手，再设置临时 `CLAUDE_CONFIG_DIR`，对真实 CLI 执行隔离的 user-scope `remove/add/get` 并核对实际写入条目，不会接触发布人员自己的 Claude 配置。只有测试前后两次迁移包校验、PowerShell AST 解析、假 CLI 回滚自检、目标 MCP 握手和真实 CLI 隔离探针全部通过并输出 `WINDOWS POWERSHELL 5.1 RELEASE GATE PASSED`，才可正式放行。没有预先 PASS 证据但经明确要求生成的交叉构建包必须标为自检候选；顶层 launcher 会自动运行同一门禁，门禁失败不会进入安装阶段。

向导只写当前用户 `%LOCALAPPDATA%\IntranetBrowserAgent` 和 Claude Code user-scope 配置。运行版本目录已存在时只在完整性验证通过后复用，绝不覆盖。每次配置变更写入带时间和随机后缀的备份目录；若用户级 MCP 注册失败，恢复变更前的 Claude Code 用户配置。

以下手工步骤用于生产、故障诊断或向导不可用时的受控回退：

1. 先验证外层企业签名。
2. 在解压前用 `Get-FileHash .\<transfer.tar.gz> -Algorithm SHA256` 与相邻 `.sha256` 比对。
3. 用 Windows 自带 `tar.exe -xzf .\<transfer.tar.gz>` 解压迁移包，进入其顶层目录，先执行 `py -3 .\toolkit\scripts\verify-bundle.py .`，再执行 `py -3 .\toolkit\scripts\verify-bundle.py .\runtime\<runtime.tar.gz>`。Windows 运行包的 `SYMLINKS.json` 必须为空。
4. 读取 `KIT-METADATA.json` 中嵌入的运行包构建元数据，确认目标为 `windows/x64` 且 `bundledNode=true`。验证解压目录的 `node` 子目录只含 `node.exe`、`LICENSE`、`VERSION`、`SOURCE.json`，并执行 `validate_node_distribution.py --approval-file .\toolkit\config\windows-node-sources.json`。若 `crossBuilt=true`，它只能进入试点验证，不能进入生产制品库。
5. production 将运行包解压到新的版本目录，例如 `C:\ProgramData\IntranetBrowserAgent\releases\browser-agent-runtime-1.0.9-core-windows-x64`；user-scope pilot 则使用 `%LOCALAPPDATA%\IntranetBrowserAgent\releases\...`。再次对解压后的运行目录执行同一校验器，再与其 `BUILD-METADATA.json` 对照。
6. 不直接覆盖当前版本。完成预检后，再由配置管理把 `C:\ProgramData\IntranetBrowserAgent\current` junction 切到新版本目录。

迁移包顶层 `START-HERE.md` 和 `toolkit/docs/windows-quickstart.md` 给出试点最短操作路径；若与本文冲突，以本文为准。

## 3. 部署清单和配置

1. 自动 Windows pilot 不需要人工复制清单；向导根据当前用户 `%LOCALAPPDATA%` 生成 `mcpScope=user`、`workspaceRoots=[]` 的清单。生产从 `toolkit/config/deployment.windows-production.json.template` 复制，复制件放在迁移包目录之外，替换本机盘符路径和全部占位符，不配置 UNC 路径。
2. Windows pilot 清单省略 `network`，因此不限制网址；不绑定任何项目目录。向导自动写入实际识别到的 Chrome/Edge `.exe`，不要求用户填写；最终 MCP 同时固定 `--browser=<channel>` 与 `--executable-path=<browser.exe>`，并由浏览器使用 last-used Profile。
3. 仅 production 清单需要列出精确 origins，并填写网络强制层、负责人、变更记录、网页数据分类、模型路由及组织实际要求的批准记录，把 `approved` 改为 `true`。
4. 运行 `validate`，再运行 `render`。
5. Windows pilot 由向导使用 Claude Code CLI 注册 user-scope stdio server，不复制 `.mcp.json` 到项目。production 再按选择的 project/managed scope部署生成配置。
6. Windows pilot 不修改任何项目 `CLAUDE.md`；production 如需采用 `CLAUDE.browser.md`，按项目规范评审合并。

项目 `.mcp.json` 的首次批准只适用于 project scope。Windows 通用 pilot 使用 user scope；生产终端可由管理员进一步使用 Claude Code managed MCP 配置或 MCP allow/deny policy 锁定服务器集合。

## 4. 浏览器身份

### persistent

1. Windows Profile、配置和输出目录使用 NTFS ACL，只授权专用账号、SYSTEM 和必要管理员；用 `icacls` 或企业端点管理证据验收，不使用 POSIX `0700` 描述 Windows 权限。
2. 首次有头启动后，由用户完成 SSO/MFA；Agent 此时暂停。
3. 验证企业证书、代理、PAC 和强制扩展都已应用。
4. 同一 Profile 不得由第二个 Agent 或另一个 Chrome 实例打开。

### extension

1. Windows pilot 只使用迁移包内经固定哈希与逐文件校验的官方 CRX/已解压副本：优先自动策略安装，失败时在同一向导中人工“加载已解压的扩展程序”；production 必须通过 managed Web Store 或经组织批准的自托管 CRX 策略。
2. 默认保留每次连接批准和 Tab 选择。
3. 不把 `PLAYWRIGHT_MCP_EXTENSION_TOKEN` 写入仓库。无人值守例外需单独审批和秘密注入。

### cdp

1. 只使用 Chrome channel 或 loopback endpoint。
2. 远程调试打开期间不得复用个人 Profile。
3. 任务结束关闭远程调试，并确认端口不再监听。

## 5. 日常运行

- 开始前运行 `preflight`。它会先确认主机确为 Windows x64；user scope 不绑定项目根，project/managed scope 才核对 `workspaceRoots`。在尚未复制到最终目录的发布验证中，用 `--runtime-root` 和 `--config-root` 指向暂存目录；project/managed scope 需要时再使用 `--project-root`。
- 人确认当前账号、租户和环境；生产与测试必须视觉上可区分。
- Agent 每次只做一步并重新观察。
- 高风险动作在最后一步前由人确认。
- 遇到身份漂移、页面注入指令、TLS 警告或并发控制迹象立即停止；production 还应在导航到未列出的 origin 时停止。
- 不需要的截图、下载和日志在任务结束后按清单保留策略清理。

## 6. 升级与回滚

升级必须新建版本目录，重新构建、扫描并跑全部验收。不得在内网运行 `pnpm update`、`npm install` 或 `npx ...@latest`。

user-scope pilot 回滚时，停止 Claude Code 会话，恢复备份的用户配置或用 Claude Code CLI 将 `intranet-browser-agent` 重新指向上一已验证版本，再恢复对应 Playwright 配置并运行 preflight。production 若采用 `current` junction，则由配置管理切回上一版本。Profile 数据格式如果被浏览器升级迁移，不能假定旧 Chrome 可安全读取；浏览器自身回滚由企业浏览器运维流程负责。

## 7. 事件处理

发现异常导航、秘密暴露、意外写操作或制品完整性失败时：停止 Agent，隔离终端，保留最少必要证据，吊销相关会话/token，按企业事件响应流程上报。不要为了“继续任务”绕过网络或浏览器警告。
