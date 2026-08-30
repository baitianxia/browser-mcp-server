# ADR-0001：本机 stdio 与专用 Profile

状态：已接受。

## 背景

目标页面依赖企业 SSO、MFA、证书、代理和浏览器策略，同时运行环境可能无法访问公网软件源。

## 决策

Playwright MCP 与 Chrome 在同一终端或 VDI 运行，由 Claude Code 使用 stdio 启动。生产默认使用专用持久化 Chrome Profile；Extension 仅在已有企业扩展治理时启用；CDP 仅允许本机回退。一个 Profile 同时只允许一个 Agent。

## 结果

优点是没有可远程发现的 MCP 服务、认证由人完成、离线部署简单、身份边界清晰。代价是需要终端级部署、每个 Profile 独立占用磁盘，而且跨主机集中调度不在 V1 范围。
