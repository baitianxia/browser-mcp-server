# 架构规范

状态：当前、规范性文档。

## 目标与边界

`browser-mcp-server`（显示名“浏览器助手”）为当前用户的 Claude Code 提供本地 stdio 浏览器能力。MCP 注册名固定为 `browser-mcp`。它不提供自动输入密码、OTP、Passkey 或验证码，不共享跨主机浏览器，不监听网络端口，也不把提示词、URL 过滤或 loopback 当作安全隔离。

Windows V1 只支持 x64、本机盘符路径和当前用户范围。历史工程名、MCP 别名、共享目录和旧配置迁移不属于当前版本；新版本从干净的 `browser-mcp-server` 用户根目录开始。

## 组件与信任边界

```text
Claude Code ── user-scope stdio ── node.exe + 固定兼容层
                                      │
                                      └─ 同包 Playwright MCP
                                           │
                         Extension ───────┴────── persistent
                         用户批准标签页             独立 Profile
```

兼容层和固定 Playwright MCP 位于同一个离线运行包。兼容层只负责配置读取、MCP 身份、快照策略、页面稳定等待和受检的交互回退；它不扩大 Node 文件权限、网络权限或浏览器认证边界。高风险动作由 Claude Code 确认和企业业务审批共同约束。

- **文件边界**：运行时、配置、输出、扩展副本、维护工具和备份只在 `%USERPROFILE%\browser-mcp-server` 内；Chrome/Edge 的 `LOCALAPPDATA` 只用于发现既有浏览器 Profile 和检查扩展状态。
- **身份边界**：Extension 模式由用户选择具体标签页；persistent 模式使用本工程独立的 `browser-profile\pilot`，不读取日常 Profile。
- **网络边界**：本项目不提供网络隔离；生产网络由企业代理、防火墙、ACL 和浏览器策略负责。试点省略 `network` 清单字段，沿用固定 Playwright MCP 的默认可访问范围。
- **模型边界**：模型路由和数据处理由企业批准的 Claude Code 配置负责。

## 运行模式

### extension

初始安装采用有头 Extension 模式和逐次连接批准。安装包携带经过批准的 Playwright Extension CRX 及逐文件一致的 `unpacked` 目录；目标机不联网下载扩展。安装器先尝试当前用户的离线策略，策略或浏览器自动化不可用时显示扩展页和精确目录，等待用户在同一进程完成“加载已解压的扩展程序”。自动打开页面或复制路径失败只影响便利功能，不应跳过等待或留下临时策略。Extension 模式不能与无头或 `userDataDir` 组合。

用户可以在安装后明确选择记住扩展令牌。令牌只写入 Claude Code 当前用户 `browser-mcp` 条目的环境，不写入本工程 `settings.json`、清单、日志或响应；切回逐次批准或 persistent 时必须删除该环境值。

扩展复用以 `config/playwright-extension-source.json` 的 `compatibleVersions` 为唯一批准来源，构建时原样写入两级发布元数据，安装前必须核对三者一致。列表必须非空、无重复且包含包内版本；新增其他版本前必须记录它与固定 MCP 的真实连接验证证据，不能只凭版本号放行。当前批准列表仅包含 `0.4.0`。已启用的当前版本直接复用，其他批准版本可由用户选择保留或离线更新；未批准、禁用、权限不足或路径不可信的记录进入修复安装。选择更新后必须检测到包内当前版本才继续。检测优先使用最近使用的浏览器 Profile，`Secure Preferences` 中已有记录时不得被普通 `Preferences` 覆盖；已解压旧版只允许本工程 `browser-extension/<批准版本>/unpacked` 的精确目录，不迁移历史工程路径。

### persistent

persistent 模式始终使用 `%USERPROFILE%\browser-mcp-server\browser-profile\pilot`，由本工程独占。首次需要 SSO/MFA 时用有头模式完成登录，再按需切换无头。不得把日常 Chrome/Edge 默认 Profile 配置为该路径。

### cdp

V1 只允许 loopback 的受控调试端点或官方 channel；禁止 `0.0.0.0`、局域网地址、主机名和跨主机 WebSocket。该模式不由 Windows pilot 默认模板启用。

## MCP 与配置契约

Windows MCP 条目必须是 user-scope stdio，命令直接执行清单中已验证的 `node.exe`，首个脚本参数为固定运行包内的兼容层（当前文件名仍为内部实现 `bin\intranet-browser-agent-mcp.js`），随后由兼容层启动同包上游 CLI。不得把 `.cmd`、`.bat` 或 `npx` 作为 MCP `command`，不得在线解析或下载依赖。

部署清单固定声明 `target.os=windows`、`target.arch=x64`、`mcpScope=user` 和空 `workspaceRoots`。Extension 和 persistent 条目都绑定安装器实际识别的 Chrome/Edge `.exe`；渲染、握手、注册和最终核对必须使用同一 `--browser` 与 `--executable-path`。MCP 环境严格来自 `config/windows-mcp-environment.json`，只允许固定的空覆盖变量和心跳超时；当前用户令牌是唯一可选额外值。

设置文件为 `%USERPROFILE%\browser-mcp-server\config\settings.json`，由本工程独立维护。MCP 提供 `browser_config_status`、`browser_configure` 和 `browser_config_reload`：状态包含绝对路径、schema 版本、缺失字段和下一步命令且遮蔽秘密；配置写入使用校验、临时文件和原子替换；重载可应用交互设置，启动方式、channel 或可执行文件变化要求重启 MCP/Claude Code。

`interaction.snapshotStrategy` 只允许 `compact|full`，默认 `compact`；`interaction.compatibilityMode` 只允许 `robust|standard`，默认 `robust`。两项都渲染到配置并可由设置工具事务化切换。artifact 只能落在清单 `output.directory`，返回值提供本机绝对路径和相对元数据，不接受越界路径。

## 操作状态与权限

浏览器动作遵循 `Observe → Reason → Act → Wait/change detection → Verify`。导航、提交、Tab 切换、弹窗、SPA 路由和异步更新都会使旧观察失效。动态兼容回退只能作用于同一唯一、可见、已连接且未禁用的目标；pointer 和 clipboard 能力必须显式调用并遵守当前 origin/vision 权限。删除、上传、提交、权限变更和对外沟通属于确认动作。

## Windows 安装与升级

正式用户交付物是一个 ZIP，只有一个顶层目录，顶层入口为 `INSTALL.cmd`、`CONFIGURE.cmd`、`OPEN-CONFIG.cmd` 和 `UNINSTALL.cmd`。用户解压后只需双击 `INSTALL.cmd`。安装器不运行测试、AST 扫描、注册器自检、npm/pnpm/npx 或在线下载；它只复用当前用户已经安装且可验证的 Claude Code。

安装器先验证 ZIP/清单、内层运行包、Node 来源、扩展和浏览器，再在同卷短路径暂存并以 `[IO.Directory]::Move` 原子发布。配置先完整渲染和握手，之后才整目录切换；外部锁定时在同一进程有界退避重试，状态不明确或超时则保留旧目录并失败关闭。升级只提取当前受管清单中的浏览器模式、channel、授权、无头和交互选项，不复制旧运行路径、任意字段或历史共享目录。卸载只处理本工程根目录和当前 `browser-mcp` 条目。

## 发布与验收

构建机使用固定 Node/pnpm/Python，跳过安装脚本和浏览器下载，携带批准的 Windows x64 Node。`build-transfer-kit.py` 按审查白名单生成 ZIP、`release-manifest.json`、`KIT-METADATA.json`、逐文件 `SHA256SUMS.txt`、CRX/`unpacked` 和 `NOTICE.md`；测试、缓存、秘密、symlink/junction 和发布门禁脚本不进入公共 ZIP。

发布门禁 `scripts/verify-windows-release.ps1` 必须在原生 Windows PowerShell 5.1 运行。它验证 ZIP 和解压目录、PowerShell AST、完整 Python 测试、内层运行时、Node 版本、MCP stdio `initialize/tools/list` 及隔离真实 Claude `remove/add/get`，并在测试前后复验包。门禁由发布流水线调用，目标 launcher 不调用它。只有 `buildHost=windows/x64`、`target=windows/x64`、`crossBuilt=false` 和 `targetCliSmokeTested=true` 的制品可标为正式包。

## 变更规则

改变运行模式、信任边界、默认权限、版本、配置字段、路径、MCP 身份或放行条件时，必须同步更新本文件、部署 Schema、校验器、测试和用户文档。共享 Windows 基线见 `/Users/baitianxia/project/docs/windows-development.md`；本项目的 `browser-mcp-server` 路径和不迁移历史身份规则优先。
