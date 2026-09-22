# MCP 工程公共工程标准

- 状态：当前、规范性文档
- 版本：`1.0.0`
- 参考实现：[`standards/mcp_standards`](../standards/mcp_standards)
- 适用范围：本地 MCP 服务、安装器、配置工具、升级/卸载脚本和发布门禁

这份标准把多个 MCP 工程已经遇到的边界收敛成一个可复用契约。浏览器、邮件、数据库和 workspace 工程可以有不同的业务工具，但 Claude Code 入口发现、用户级注册、stdio 握手、回滚和 Windows 发布证据必须遵循同一套规则。项目专属的产品名、MCP 名称、配置目录、浏览器参数和工具白名单仍由项目自己的架构文档声明。

## 盘点结论

本次对现有工程的审计形成了以下结论：

| 工程 | 已有经验 | 标准化缺口 |
| --- | --- | --- |
| `browser-mcp-server` | 有 user-scope 注册事务、字节回滚、stdio smoke 和 Windows 发布门禁 | 原实现只在本项目脚本中，其他工程无法直接复用 |
| `mail-mcp-server` | Windows 候选、npm/NVM/WinGet 链接、PE 检查和历史 Windows gate 最完整 | 需要把 PowerShell 规则沉淀为跨平台参考实现 |
| `database-mcp-server` | 有安装流程和基础 `Get-Command` 探测 | 缺少 WindowsApps、最终路径、npm 包身份、PE/能力探针和完整诊断 |
| `claude-workspace-client` | 已处理 WindowsApps，支持 native/shim 的启动分流 | 缺少 npm 包身份、链接、PE、版本/能力和注册事务契约 |

结论不是“每个工程再加一个脚本”，而是把公共边界放在版本化参考实现中，再让各工程按 conformance level 升级。历史 Windows gate 只能作为证据来源，不能自动替代当前分支的验收。

## 1. 项目接入契约

每个 MCP 工程必须在自己的当前架构文档中声明：

1. MCP server name、显示名和版本来源；
2. transport（本标准默认 local stdio）和 scope（本标准默认 Claude user scope）；
3. command、args、env 的最终形状，以及哪些字段允许用户配置；
4. `initialize` 支持的 MCP protocol versions 和 required tools；
5. 用户配置文件路径、备份路径、安装根目录和升级/卸载范围；
6. Windows 支持的架构、Node 来源、真实 Windows gate 和发布证据位置。

公共库负责边界和验证，不能替项目猜测这些值。改变产品身份、路径、权限、transport 或注册 scope 时，必须同时更新项目架构、验收和用户文档。

## 2. Claude Code 探测

探测返回结构化 `DiscoveryResult`，至少包含候选来源、原始命令路径、最终路径、实际 executable、prefix、入口类型、`--version` 结果、`mcp --help` 结果和拒绝原因。安装、设置、升级、卸载和发布门禁共享同一个结果；路径、文件指纹、用户或配置目录变化时缓存失效。

自动候选顺序固定为：显式路径、Windows `where.exe` 返回的所有结果、已知用户/安装目录、当前 `PATH`。显式路径存在但验证失败时必须停止并报告原因，不能静默换用另一个 Claude。探测不得扫描整盘、调用登录 shell 或运行安装修复命令。

Windows 候选必须：

- 规范化绝对路径并保留原始路径；
- 通过文件句柄解析最终路径，允许外部 npm/NVM/WinGet 的链接在最终目标安全时继续；
- 拒绝 `Microsoft\\WindowsApps` 和 `Program Files\\WindowsApps` 应用别名、UNC、目录、断链和无法解析的路径；
- 对原生入口执行 PE 和 AMD64/ARM64 检查；
- 对 npm `@anthropic-ai/claude-code` 使用严格 UTF-8 读取 `package.json`，精确核对 `name` 和 `bin`，并确保 bin 路径留在包目录内。

入口分流必须保持清晰：

- 声明的 `.exe` 是原生 Claude Code，直接执行，prefix 为空，不能交给 Node；
- `.js`、`.cjs`、`.mjs` 使用已经验证的 Node 直接执行；
- `.cmd`、`.bat`、`.ps1` 只能用于发现或解析，不能作为 MCP 子进程 executable；
- 探测成功还必须通过有界 `--version` 和 `mcp --help`，stderr 警告不能单独判失败，真实非零退出码、超时和空能力必须保留在诊断中。

这部分细则的公共所有者是 [`windows-claude-code-discovery.md`](../../docs/windows-claude-code-discovery.md)。参考实现不会运行 npm、pnpm、npx、WSL，也不会联网安装 Claude Code 或 Node。官方 Claude Code 文档说明了 `claude mcp add/list/get/remove` 和 user scope 的命令边界，可参阅 [Claude Code MCP 文档](https://code.claude.com/docs/en/mcp)。

## 3. 用户级注册和卸载

注册必须是一个可回滚事务：

1. 在第一次真实修改前保存 Claude 用户配置的字节快照；
2. 用同一个已验证的 Claude invocation 执行 `mcp remove <name> --scope user`；
3. 只有退出码为 `1` 且完整输出精确匹配“指定名称不存在于 user scope”的诊断时，才把缺失视为首次安装；
4. 执行 `mcp add --transport stdio --scope user ... -- <command> <args...>`；
5. 重新读取 JSON，核对 `command`、`args` 和 `env`；
6. 执行 `mcp get <name>`，确认 CLI 能读取最终条目；
7. 任一步骤失败，逐字节恢复原配置并以非零退出。

安装器不能旁路写 JSON 来冒充 CLI 注册成功。公共实现的 `unregister_user_mcp` 要求调用方提供期望的 `command/args/env`，会在执行 remove 前核对现有条目；无法确认目标属于本工程时停止，不删除其他 MCP。备份应使用唯一事务路径，保留 CRLF/LF、BOM 和末尾字节。

## 4. MCP stdio 协议

stdio 子进程必须满足 [MCP transport 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)：stdin/stdout 使用 UTF-8、每行一个 JSON-RPC 消息；stdout 只能有协议消息，日志写 stderr。冒烟工具应：

1. 在有界时间内发送 `initialize`，携带项目支持的 protocol version；
2. 验证返回版本属于项目允许集合，并核对 `serverInfo.name` 与非空 `serverInfo.version`；
3. 发送 `notifications/initialized`；
4. 请求 `tools/list`，验证工具列表结构、名称唯一性和项目 required tools；
5. 超时、断管、非法 UTF-8、非法 JSON、非 JSON-RPC 消息、身份不符和缺少工具都失败关闭，并清理子进程。

初始化顺序和版本协商遵循 [MCP lifecycle 规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)。stdio smoke 不应启动浏览器或访问业务网络；它只证明 MCP 入口、协议和工具目录可用。

## 5. 配置、升级和发布

项目配置必须有 schema、严格校验、临时文件和原子替换。安装/升级先完成清单、运行时、Claude 探测、配置渲染和 stdio smoke，再原子切换受管目录；外部锁定或状态不明确时保留旧版本并失败关闭。卸载只处理当前项目根目录和当前项目的 MCP 条目。

Windows 发布 gate 至少覆盖：原生 PowerShell/系统版本、x64 PE、WindowsApps、链接/ junction、npm native 与 JS bin、含空格/中文路径、真实 Claude `remove/add/get`、MCP `initialize/tools/list`、失败回滚和包完整性。跨平台 Python 测试只能称为代码覆盖；没有 Windows 实机或 CI 证据时，必须明确写“Windows 未验证”。目标机不得执行 npm、pnpm、npx 或发布测试。

## 6. 一致性等级

后续工程用以下等级记录迁移状态：

- **L0 文档**：项目已声明身份、scope、路径、transport 和 Windows 支持边界，并引用本标准；
- **L1 公共实现**：接入 `mcp_standards` 的 discovery、registration、stdio，并补项目 required tools 测试；
- **L2 Windows gate**：完成 native/npm/link/WindowsApps/PE/注册回滚矩阵和真实 Windows gate；
- **L3 发布阻断**：发布流程在 L2 通过前阻止正式包，保留日志、清单、哈希和可复现证据。

新工程默认从 L0 开始，已有自定义 resolver 的工程必须先列出差异，再迁移到 L1；不能以“当前机器能启动”代替 L2。

## 7. 迁移清单

每个工程升级时按以下顺序执行，并把结果写入自己的验证文档：

1. 读取本标准、共享 Windows 探测规范和项目当前架构/安装/验收文档；
2. 用项目真实值补齐接入契约；
3. 删除重复的 Claude 探测逻辑，改为调用公共 resolver；
4. 接入用户级注册事务和 stdio smoke；
5. 把出现过的错误添加为可回归 fixture（例如 missing-entry 的精确诊断、WindowsApps、npm native `.exe`、链接和回滚）；
6. 在 Windows gate 中记录通过/跳过/未验证项，不能只贴成功日志；
7. 更新项目架构、安装、验收、验证和问题记录；
8. 以公共实现版本或 Git commit 固定依赖，避免工程之间悄悄分叉。

当前这份参考实现的 Python 测试覆盖探测优先级、missing-entry 精确匹配、注册回滚、stdio 握手和 CLI 包装；真实 Windows 路径和 Claude Code 安装仍需各项目在 Windows gate 中证明。
