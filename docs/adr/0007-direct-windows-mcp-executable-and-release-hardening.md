# ADR 0007：Windows MCP 直接启动固定可执行文件并收紧发布事务

状态：已接受，2026-08-30。

## 背景

1.0.7 复核发现：安装器能在 PowerShell 中成功执行 `playwright-mcp.cmd --help`，并不能证明 Claude Code 重启后也能拉起同一个 `.cmd`。Windows 上 Node 子进程直接启动 `.cmd`/`.bat` 会受 shell shim、`spawn EINVAL` 和命令行元字符影响；Claude Code 上游也有相同类别的 Windows MCP 报告。继续把 `.cmd` 写成 MCP `command` 会留下“安装成功、实际连接失败”的盲区。

同次复核还发现，原最小 Node 校验只证明 `SOURCE.json` 与文件自洽；若构建输入本身伪造，校验器没有本项目固定的外部期望哈希可供比较。运行时直接解压到最终版本目录、配置逐文件覆盖以及测试后才校验迁移包，也会降低中断恢复和门禁可信度。固定版本 Playwright MCP 会在读取配置文件后合并 `PLAYWRIGHT_MCP_*` 环境变量，因此调用者遗留环境还可能让“已校验配置”和实际运行行为不一致。

## 决策

- Windows 清单必须声明本机盘符绝对路径 `nodeExecutable`。渲染出的 MCP 直接执行已校验的 `node.exe`，首个参数是固定运行包内的 `node_modules\@playwright\mcp\cli.js`，不再把 `.cmd` shell shim 作为 Claude MCP 命令。`.cmd` 只保留为构建/人工诊断入口。
- Windows 一键迁移包必须 `bundledNode=true`，不探测、不依赖也不修复系统 Node。Claude Code 必须提供原生 `claude.exe`；旧式 `claude.cmd` 不进入此自动流程。
- `config/windows-node-sources.json` 是当前发布批准的 Node 来源哈希清单。准备、构建和目标安装都必须同时校验官方 archive、官方 `SHASUMS256.txt`、`node.exe` 与 `LICENSE` 的固定 SHA-256；自洽但未批准的来源失败关闭。
- 注册事务除 `remove → add → get` 外，还必须直接读取隔离/真实的 Claude 用户配置，确认 user-scope 条目准确指向已验证的 `node.exe`、固定 CLI 和 Playwright 配置；不以可能受同名高优先级 scope 影响的 `get` 结果代替该检查。
- `config/windows-mcp-environment.json` 固定 Windows Playwright 子进程环境：清空固定依赖支持的配置覆盖变量以及 `NODE_OPTIONS`/`NODE_PATH`，保留默认心跳，不写扩展 token 或秘密。渲染、协议握手、Claude `--env` 注册和最终条目核对必须共用该映射。只有明确的“user-scope 条目不存在”可以忽略 `remove` 非零；其他错误必须回滚。
- 运行时先解压到同盘唯一暂存目录、完整性校验通过后再原子发布版本目录。生成配置先在 `%LOCALAPPDATA%` 内暂存并 preflight，再整目录切换；后续失败恢复旧配置目录和 Claude 用户配置。
- 写入前解析 `%LOCALAPPDATA%` 下全部目标路径，拒绝越界和子级 link/junction；运行时使用短暂存路径降低传统 Windows 路径长度风险。发布门禁只能运行并校验同一解压目录，在执行打包测试前后各验证一次迁移包，且所有 Python 进程禁止生成 bytecode；它还必须临时解压内层运行时，用目标 Node 完成真实 MCP stdio `initialize + tools/list` 握手。
- 1.0.7 撤回；这些约束从 1.0.8 起生效。

## 结果

Claude Code 的真实 MCP 启动路径不再依赖 Windows shell shim，构建输入、环境继承、路径边界、中断恢复和门禁污染都有机器可执行的失败关闭条件。代价是 Windows 一键包必须携带批准的最小 Node、写入较长但固定的 MCP 环境映射，且只支持原生 Claude Code 安装；这些限制与“内网目标不运行 npm/pnpm/npx、只双击一次”的目标一致。
