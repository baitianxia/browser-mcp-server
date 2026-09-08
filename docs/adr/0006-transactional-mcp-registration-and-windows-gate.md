# ADR 0006：事务化 MCP 注册与 Windows 发布门禁

状态：已接受，2026-08-30。

注：ADR 0008 已取代本文中“目标安装器运行注册器 `self-test`”和“目标 launcher 运行完整发布门禁”的决定；事务化注册与回滚要求继续有效。当前发布门禁由发布方在精确公共 ZIP 上单独运行，目标机 `INSTALL.cmd` 只执行目标安装与必要的无副作用安装前检查。

> 2026-09-07 统一 Windows 交付更新：如本文与当前用户交付冲突，以 `docs/architecture.md`、`docs/acceptance.md` 和 `docs/operations.md` 为准。当前用户包身份为 `browser-mcp-server` / `browser-mcp`，公共入口为 `INSTALL.cmd`，安装根目录为 `%USERPROFILE%\browser-mcp-server`；本文的旧身份和旧入口只用于历史证据。

## 背景

1.0.4 的首次安装在 Windows PowerShell 5.1 中把 Claude CLI 的正常 stderr 提示提升成终止错误，真实的 `mcp add` 尚未执行就停止。该缺陷只靠 macOS 静态源码检查没有被发现，最终由内网试点人员承担了发现成本。修补某一条 PowerShell stderr 分支不能解决“跨平台构建侧无法执行高风险注册事务”的系统问题。

## 决策

- PowerShell 安装器不再直接执行 `claude mcp remove/add/get`。
- 新增 `register_claude_user_mcp.py`，以进程退出码为唯一成败依据，固定执行 user-scope `remove → add → get`。
- 事务开始前逐字节备份 `.claude.json`；`add`、`get` 或进程执行失败时恢复原字节。原配置不存在时，失败后删除事务新建的配置。
- 缺少旧条目的 `remove` 非零是首次安装正常分支；成功进程写 stderr 不是失败。
- 注册模块提供无外部依赖的 `self-test`，在临时目录通过假 Claude CLI 覆盖首次安装、成功 stderr、升级、`add` 失败和 `get` 失败回滚。Windows 安装器在接触真实用户配置前强制运行该自检。
- 新增 `verify-windows-release.ps1`。正式放行前，必须在装有实际 Claude Code 的受控 Windows x64、Windows PowerShell 5.1 Desktop 上执行完整测试、PowerShell AST 解析、假 CLI 注册自检、临时 `CLAUDE_CONFIG_DIR` 下的真实 CLI 隔离 `remove/add/get` 探针和制品校验。ADR-0007 进一步要求门禁实测包内目标 Node 和 MCP stdio 握手。
- 顶层 `INSTALL-WINDOWS-PILOT.cmd` 强制把同一门禁串在安装之前。尚无预先 Windows 门禁证据但经明确要求生成的交叉构建包，只能标为“自检候选”；一次双击先运行无持久化副作用的门禁，全部通过才进入安装。它不能冒充正式放行制品。
- 1.0.6 的 Windows 门禁暴露出测试夹具换行缺陷：`Path.write_text()` 在 Windows 把 LF 转为 CRLF，而测试把备份与硬编码 LF 比较。所有字节保真夹具改用 `write_bytes()`，回归同时覆盖 LF 与 CRLF；不允许通过文本读取或换行归一化掩盖备份字节差异。

## 结果

Claude 配置变更的高风险逻辑可以在构建侧重复自动测试，且目标安装前还会无副作用自检。仍然需要一个受控 Windows runner 证明 Windows PowerShell、目标 `node.exe + bin\intranet-browser-agent-mcp.js + 同包上游 CLI`、Claude CLI 和目标 Python 行为（入口由 ADR-0010 更新）；没有该 runner 时只能交付明确标识且自动失败关闭的自检候选，不能声称已经过 Windows 验证或用于生产。
