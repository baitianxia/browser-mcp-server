# MCP 工程公共标准实现

这里是跨项目复用的参考实现，当前版本为 `1.0.0`。它不安装 Claude Code、Node、npm 包或浏览器，也不修改用户 PATH；它只验证调用方已经提供的外部依赖，并对 MCP 子进程做有界检查。

实现分为三层：

- `mcp_standards.claude_code`：收集并验证 Claude Code 候选，区分原生 `.exe` 与 npm JavaScript 入口，执行 `--version` 和 `mcp --help` 探针；
- `mcp_standards.registration`：以用户 scope 执行 `remove → add → 配置核对 → get`，失败时按字节恢复 Claude 用户配置；卸载前要求核对期望条目，避免删掉同名的其他服务；
- `mcp_standards.stdio`：执行 `initialize → notifications/initialized → tools/list`，验证 JSON-RPC、协议版本、server identity、版本和工具名。

## 在其他 MCP 工程中接入

先把 `standards/` 作为已审查的版本化依赖引入工程，再由项目文档声明自己的 `server_name`、命令、参数、环境和 required tools。不要复制其中一段 resolver 到每个工程里；公共行为的修改应先更新这里和规范文档，再由各工程升级引用。

开发机可以直接运行：

```bash
PYTHONPATH=standards python3 -m mcp_standards.cli discover-claude
PYTHONPATH=standards python3 -m mcp_standards.cli discover-claude --explicit-path "C:/path/to/claude.exe"
PYTHONPATH=standards python3 -m mcp_standards.cli smoke-stdio \
  --expected-server-name my-mcp \
  --required-tool health \
  -- python server.py --stdio
```

Python 调用示例：

```python
from pathlib import Path

from mcp_standards import discover_claude, smoke_stdio

discovery = discover_claude(explicit_path=Path(r"C:\Tools\claude.exe"))
if discovery.selected is None:
    raise RuntimeError(discovery.diagnostics)

report = smoke_stdio(
    ["node", "server.js"],
    expected_server_name="my-mcp",
    required_tools=("health",),
)
```

Windows 安装器、设置工具、升级/卸载脚本和发布门禁必须共享同一个 `DiscoveryResult`，不能每一步重新猜测入口。目标机不得运行 npm、pnpm、npx 或在线修复命令；没有通过探测就停止在真实配置写入之前。

公共约束、验收等级和迁移清单见 [`docs/mcp-engineering-standard.md`](../docs/mcp-engineering-standard.md)。Windows Claude Code 的候选、链接、PE、npm 包身份和能力探针细则见共享规范 [`windows-claude-code-discovery.md`](../../docs/windows-claude-code-discovery.md)。

这套实现目前在本仓库的跨平台 Python 测试中验证；Windows 原生路径、链接、PE 和真实 Claude Code 的证明仍必须由项目自己的 Windows gate 提供，不能把本地测试描述成 Windows 已验收。
