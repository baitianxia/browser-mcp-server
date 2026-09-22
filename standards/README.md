# 公共 MCP 标准实现

公共 Claude Code 探测、user-scope 注册事务和 MCP stdio 冒烟实现已迁移到独立的公共 Python 工具包：

[`baitianxia/mcp-engineering-standards`](https://github.com/baitianxia/mcp-engineering-standards)

browser 工程只保留自己的 `browser-mcp` 身份、配置路径、固定 Playwright 运行包和 required tools。不要在这里重新复制 resolver；升级时固定公共包的 Git tag 或 commit，并按本工程的 Windows L2/L3 门禁验证真实 Claude Code、配置和发布包。

```bash
python -m pip install \
  "git+https://github.com/baitianxia/mcp-engineering-standards.git@v1.0.0"
```

Python import 包名为 `mcp_engineering_standards`。公共契约、迁移等级和 Windows 规则见本仓库的 [`docs/mcp-engineering-standard.md`](../docs/mcp-engineering-standard.md) 以及独立工程的 [engineering standard](https://github.com/baitianxia/mcp-engineering-standards/blob/v1.0.0/docs/engineering-standard.md)。
