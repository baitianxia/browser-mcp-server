# ADR 0007：Windows MCP 直接启动固定可执行文件并收紧发布事务

状态：已接受；2026-09-01 按 ADR-0009 补充 Windows pilot 独立 Profile 与用户令牌例外，Playwright 首个脚本参数按 ADR-0010 更新。

> 2026-09-07 统一 Windows 交付更新：当前用户路径、包名、MCP 名称和入口以 `docs/architecture.md`、`docs/acceptance.md` 和 `docs/operations.md` 为准，取代本文中旧的 `%LOCALAPPDATA%` 根目录或历史身份描述。当前根目录是 `%USERPROFILE%\browser-mcp-server`，公共 MCP 名称是 `browser-mcp`；本文关于直接 `node.exe`、完整性和事务边界的约束继续有效。

## 背景

1.0.7 复核发现：安装器能在 PowerShell 中成功执行 `playwright-mcp.cmd --help`，并不能证明 Claude Code 重启后也能拉起同一个 `.cmd`。Windows 上 Node 子进程直接启动 `.cmd`/`.bat` 会受 shell shim、`spawn EINVAL` 和命令行元字符影响；Claude Code 上游也有相同类别的 Windows MCP 报告。继续把 `.cmd` 写成 MCP `command` 会留下“安装成功、实际连接失败”的盲区。

同次复核还发现，原最小 Node 校验只证明 `SOURCE.json` 与文件自洽；若构建输入本身伪造，校验器没有本项目固定的外部期望哈希可供比较。运行时直接解压到最终版本目录、配置逐文件覆盖以及测试后才校验迁移包，也会降低中断恢复和门禁可信度。固定版本 Playwright MCP 会在读取配置文件后合并 `PLAYWRIGHT_MCP_*` 环境变量，因此调用者遗留环境还可能让“已校验配置”和实际运行行为不一致。

## 决策

- Windows 清单必须声明本机盘符绝对路径 `nodeExecutable`。渲染出的 MCP 直接执行已校验的 `node.exe`；按 ADR-0010，首个参数现在是固定运行包内的 `bin\intranet-browser-agent-mcp.js`，由它启动同包 `node_modules\@playwright\mcp\cli.js`。不得把 `.cmd` shell shim 作为 Claude MCP 命令；`.cmd` 只保留为构建/人工诊断入口。
- Windows `extension` 清单及 user-scope pilot 的独立 `persistent` 清单还必须声明安装器自动识别的本机 Chrome/Edge `.exe`。渲染、握手和 Claude user-scope 注册必须同时使用精确 `--browser=<channel>` 与 `--executable-path=<browser.exe>`。Extension 中这是固定 MCP 版本连接手工 unpacked 扩展的兼容路径：未指定 executable 时，上游安装预检不能识别只存在于 `Secure Preferences` 的记录；指定后则由浏览器自身打开 last-used Profile。安装检测因此只能接受 last-used Profile，不能从其他休眠 Profile 误放行。
- Windows 一键迁移包必须 `bundledNode=true`；Playwright MCP 不探测、不依赖也不修复系统 Node。Claude Code 本身是目标机已经存在的前置条件，安装器不得安装、升级、替换或修复它。发布门禁和安装器共用一个 Claude 入口探测器：显式门禁路径优先且错误时失败关闭；未显式指定时依次检查当前 `PATH` 的 `claude.exe`、npm 生成的 `claude.cmd`，最后检查官方 `%USERPROFILE%\.local\bin\claude.exe`。原生入口直接执行。npm 入口只接受同级标准 `node_modules\@anthropic-ai\claude-code\package.json` 声明的 `claude` bin，并解析该 npm 安装本来使用的同级或 `PATH` `node.exe`，随后直接执行 `node.exe + 已安装 cli.js`；不把 `.cmd` 交给 Python 子进程，不经 `cmd.exe` 拼接参数，也不运行 npm/pnpm/npx。找不到可验证入口时在持久化安装前停止。
- `config/windows-node-sources.json` 是当前发布批准的 Node 来源哈希清单。准备、构建和目标安装都必须同时校验官方 archive、官方 `SHASUMS256.txt`、`node.exe` 与 `LICENSE` 的固定 SHA-256；自洽但未批准的来源失败关闭。
- 注册事务除 `remove → add → get` 外，还必须直接读取隔离/真实的 Claude 用户配置，确认 user-scope 条目准确指向已验证的 `node.exe`、固定 CLI 和 Playwright 配置；不以可能受同名高优先级 scope 影响的 `get` 结果代替该检查。
- `config/windows-mcp-environment.json` 固定 Windows Playwright 子进程基础环境：清空固定依赖支持的配置覆盖变量以及 `NODE_OPTIONS`/`NODE_PATH`，保留默认心跳，不写扩展 token 或秘密。渲染和协议握手始终使用该映射；Claude `--env` 注册与最终条目默认也完全一致。ADR-0009 仅允许用户明确选择当前用户扩展授权时在最终 user-scope 条目增加一个已验证 token，切换模式后删除。只有明确的“user-scope 条目不存在”可以忽略 `remove` 非零；其他错误必须回滚。
- 运行时先解压到同盘唯一暂存目录、完整性校验通过后再原子发布版本目录。生成配置先在 `%LOCALAPPDATA%` 内暂存并 preflight，再整目录切换；后续失败恢复旧配置目录和 Claude 用户配置。
- 写入前解析 `%LOCALAPPDATA%` 下全部目标路径，拒绝越界和子级 link/junction；运行时使用短暂存路径降低传统 Windows 路径长度风险。发布门禁只能运行并校验同一解压目录，在执行打包测试前后各验证一次迁移包，且所有 Python 进程禁止生成 bytecode；它还必须临时解压内层运行时，用目标 Node 完成真实 MCP stdio `initialize + tools/list` 握手。
- 1.0.7 撤回；这些约束从 1.0.8 起生效。

## 结果

Claude Code 启动 Playwright MCP 的真实路径仍不依赖 Windows shell shim；构建输入、环境继承、路径边界、中断恢复和门禁污染都有机器可执行的失败关闭条件。Windows 一键包必须携带批准的最小 Node、写入较长但固定的 MCP 环境映射，但可以复用原生或标准 npm 安装的 Claude Code。npm 兼容只增加对现有安装元数据和 Node/CLI 入口的只读解析，不把包管理器或在线修复带入内网流程。
