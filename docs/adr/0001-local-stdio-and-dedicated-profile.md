# ADR-0001：本机 stdio 与专用 Profile

状态：已接受。

## 背景

目标页面依赖企业 SSO、MFA、证书、代理和浏览器策略，同时运行环境可能无法访问公网软件源。

## 决策

Playwright MCP 与 Chrome 在同一终端或 VDI 运行，由 Claude Code 使用 stdio 启动。生产默认使用专用持久化 Chrome Profile；生产 Extension 仅在已有企业扩展治理时启用；CDP 仅允许本机回退。一个 Profile 同时只允许一个 Agent。

Windows 通用内网 pilot 是有意限定的例外：固定使用 Extension 连接人明确批准的现有 Tab，以验证目标机当前 Chrome/Edge 登录态。试点迁移包携带经固定哈希验证的官方 CRX 和逐文件一致的已解压副本；本地策略安装失败时在同一向导内人工加载，默认不使用连接 token。该例外只面向测试账号和非生产数据，不改变生产专用身份边界。

## 结果

优点是没有可远程发现的 MCP 服务、认证由人完成、离线部署简单、身份边界清晰。代价是需要终端级部署、每个 Profile 独立占用磁盘，而且跨主机集中调度不在 V1 范围。
