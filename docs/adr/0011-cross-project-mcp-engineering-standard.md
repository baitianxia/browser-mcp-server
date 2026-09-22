# ADR 0011：跨项目 MCP 工程公共标准

状态：已接受；2026-09-22。

## 背景

多个 MCP 工程分别实现了 Claude Code 探测、user-scope 注册和 stdio 冒烟。mail 工程积累了 Windows 链接、PE 和 npm 入口的完整经验；browser 工程积累了配置字节回滚和发布门禁；database 与 workspace 工程仍各自保留较浅的探测逻辑。经验只存在于单个仓库和一次问题修复中，导致同类错误在工程之间重复出现。

## 决定

- 在 `standards/mcp_standards` 提供无第三方依赖的参考实现，版本化发布 `discover_claude`、`register_user_mcp`/`unregister_user_mcp` 和 `smoke_stdio`；
- 在 `docs/mcp-engineering-standard.md` 维护项目接入契约、Claude Code 探测、注册事务、stdio 生命周期、Windows gate 和 L0-L3 迁移等级；
- 其他 MCP 工程固定引用该实现版本或 Git commit，声明自己的 server name、命令、配置路径和 required tools，不复制出独立 resolver；
- 项目专属的运行时、浏览器、权限和发布布局仍由项目架构文档负责，公共标准不能替代项目验收；
- Windows 外部依赖仍只复用已存在的 Claude Code/Node。目标机不得运行 npm、pnpm、npx 或在线修复命令；没有 Windows gate 证据时，只能报告跨平台代码覆盖，不能称为 Windows 已验证。

## 结果

公共边界有了唯一的代码和文档入口，新问题可以先转成公共 fixture 再由各工程升级。参考实现当前以跨平台 Python 测试证明候选、身份、bin 分流、回滚和 stdio 协议路径；Windows PowerShell 5.1、最终句柄、真实 PE 和真实 Claude Code 仍由各工程的 L2/L3 gate 验收。任何改变公共契约的修复都必须同时更新参考实现、测试和本 ADR/标准文档。
