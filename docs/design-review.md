# 设计评审

评审输入是共享对话“浏览器登录态解决方案”。结论：总体方向可行，但原方案还不足以直接作为内网生产设计。本文记录采用、修正和暂缓的部分。

## 结论

有条件通过，条件是把以下内容从建议变成强制门禁：固定依赖、模型数据边界审批、真实网络隔离、独立浏览器身份、单 Profile 单 Agent、扩展分发方案、敏感操作确认、完整性校验和可回滚发布。

## 采用

1. Playwright MCP 作为主执行面，优先使用 DOM/Accessibility，截图和坐标能力只作兜底。
2. 动态页面采用 Observe → Reason → Act → Wait → Re-observe → Verify 循环；每个会改变页面状态的动作之后重新观察。
3. 人负责 SSO、MFA、Passkey、验证码和扫码；Agent 不接收或保存认证秘密。
4. 浏览器与 MCP 就近运行，Claude Code 通过本机 stdio 启动固定版本运行时。
5. production 使用独立 Chrome Profile，禁止与个人日常 Profile 混用；Windows pilot 初始可为验证登录态复用而使用 Extension，安装后可切换独立 Profile。
6. 内网运行时不访问 npm、GitHub 或在线更新服务。
7. Chrome DevTools MCP 只在诊断阶段按需启用。

## 必须修正

### 1. URL allowlist 不是安全边界

Playwright MCP 官方明确说明 origin allow/block 规则不构成安全边界；部分跳转也不能依赖该规则拦截。它只适合减少误导航。真正的出站限制必须由企业代理、主机防火墙、网络 ACL 或隔离 VDI 实现。本套件因此要求部署清单声明外部强制层及负责人，缺失时拒绝生成生产配置。

### 2. `CLAUDE.md` 不是强制访问控制

提示词规则能显著改善操作纪律，但页面内容可能包含 prompt injection，模型也可能犯错。删除、付款、生产变更、权限变更、代表用户发消息、上传文件和最终提交仍需由 MCP 客户端或流程审批实现；不能只写一句“操作前确认”。

### 3. Extension 模式不是天然离线

内网生产必须明确选择：受管浏览器通过 Web Store 强制安装，或经过企业签名和安全评审的自托管 CRX。扩展版本还要与 MCP 版本做兼容验证。Windows 通用 pilot 不临时放宽网络访问商店，而是在有网构建区固定并验证官方 CRX，迁移包同时携带逐文件一致的已解压 payload；目标机先尝试当前用户本地策略，浏览器拒绝时由同一个向导打开扩展页并等待用户加载本地目录。这个回退解决离线试点安装，不代替生产扩展治理。

### 4. 不能把扩展令牌提交到项目配置

`PLAYWRIGHT_MCP_EXTENSION_TOKEN` 可以绕过每次连接批准，它等价于对该浏览器会话的持久连接授权。生产默认不使用该令牌；如确需无人值守，令牌必须由机器级秘密管理注入，并单独做风险批准。Windows user-scope pilot 可以在用户明确选择后把官方扩展生成的令牌保存到该用户的 Claude MCP 配置，但必须先提示风险，并在切回逐次批准或独立 Profile 时删除。令牌始终不能进入项目 `.mcp.json`、代码仓库、迁移包、部署清单或日志。

### 5. CDP 是兼容回退，不是隔离方案

`--cdp-endpoint=chrome` 和 loopback CDP 都能复用运行中的 Chrome，但调试权限覆盖面较大。本套件只允许 channel 名或 `127.0.0.1`/`[::1]` endpoint，禁止生成远程 CDP 配置。即使绑定 loopback，也仍需专用 Profile 和主机访问控制。

### 6. 数据面与模型控制面必须一起评审

浏览器只访问内网，不代表网页内容留在内网。页面快照、截图、控制台和网络信息会进入模型上下文。生产放行前必须明确数据分类、模型路由、保留策略和批准编号；未批准时配置生成器会失败关闭。

### 7. “固定版本”还需要供应链证据

仅把 `@latest` 改成版本号不够。交付物必须由锁文件构建，保留组件清单、逐文件 SHA-256、构建元数据和企业扫描结果；内网导入前再次验证。运行时禁止自动更新和遥测。

## 默认落地选择

| 场景 | 模式 | 原因 |
|---|---|---|
| 内网 VDI/开发机生产基线 | `persistent` | 无 Chrome Store 前置；独立身份边界；可由人完成企业认证 |
| Windows 通用内网 pilot 初始值 | `extension` | 离线包携带固定官方扩展；逐次批准现有 Tab；可复用目标机当前登录态 |
| Windows pilot 身份隔离/后台运行 | `persistent` | 安装后设置为独立 `%LOCALAPPDATA%` Profile；不共享原浏览器登录态；可选有头/无头 |
| 已有 Chrome Enterprise 扩展治理 | `extension` | 可选择允许的 Tab，并复用企业浏览器插件和现有会话 |
| 兼容性排障 | `cdp` | 无扩展依赖，但权限面更广，只允许本机连接 |
| 页面内部诊断 | 可选 DevTools | 获取 Network/Console/Runtime；默认不安装/不启用 |

## 当前固定版本

- `@playwright/mcp`：`0.0.79`
- `chrome-devtools-mcp`：`1.8.0`（仅 diagnostic 包）
- 构建工具：pnpm `11.19.0`
- 运行 Node.js：`>=20.19.0`

版本依据是 2026-08-29 读取的上游包清单。每次升级必须重新运行本文列出的安全和兼容性验收，不能只改版本字符串。

## 官方依据

- [Playwright MCP 配置与安全说明](https://github.com/microsoft/playwright-mcp#configuration)
- [连接现有浏览器的三种方式](https://github.com/microsoft/playwright.dev/blob/main/mcp/configuration/browser-extension.mdx)
- [Playwright Extension 安装与连接批准](https://github.com/microsoft/playwright/blob/main/packages/extension/README.md)
- [Chrome DevTools MCP 遥测、更新检查及参数](https://github.com/ChromeDevTools/chrome-devtools-mcp)
- [Claude Code MCP 配置作用域与托管配置](https://code.claude.com/docs/en/mcp)
- [Chrome Enterprise 扩展强制安装策略](https://chromeenterprise.google/policies/extension-install-forcelist/)
