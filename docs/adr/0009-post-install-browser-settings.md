# ADR-0009：Windows 安装后浏览器设置

状态：已接受。

## 背景

Windows user-scope pilot 的一键安装默认通过 Playwright Extension 复用当前 Chrome/Edge 登录态，并要求每次连接由用户确认。实际试点还需要三种可逆选择：记住当前用户的扩展授权、切换有头/无头，以及使用不共享日常浏览器登录态的独立 Profile。

这三项不能作为互不相关的布尔开关：Extension 连接的是已经运行且可见的浏览器，不能变成无头；独立 Profile 不使用 Extension，因此也不存在扩展授权方式。

## 决策

安装器继续采用安全且兼容的初始值：`extension + 有头 + 每次连接确认`。安装成功后，在 `%LOCALAPPDATA%\IntranetBrowserAgent` 安装 `BROWSER-AGENT-SETTINGS.cmd`，用户无需保留迁移包即可切换以下组合：

1. `extension + 有头 + 每次确认`：复用现有浏览器登录态。
2. `extension + 有头 + 当前用户令牌`：用户从官方扩展连接页复制一次令牌，设置工具将其注册到 Claude Code 当前用户 MCP 环境；后续连接不再显示批准页。
3. `persistent + 独立 Profile + 有头/无头`：Profile 固定在 `%LOCALAPPDATA%\IntranetBrowserAgent\browser-profile\pilot`，不读取日常 Chrome/Edge Profile。首次需要交互登录时先使用有头模式，完成后可切换无头。

扩展令牌不得进入迁移包、部署清单、项目文件、日志或命令输出；它只允许存在于当前用户 Claude Code MCP 配置中。切回“每次确认”或独立 Profile 时，注册事务必须删除该环境项。彻底吊销曾泄漏的令牌时，用户还需在官方扩展页重新生成令牌并重启浏览器。

设置变更必须复用安装锁、`%LOCALAPPDATA%` 路径检查、暂存 preflight、MCP stdio 握手、配置整目录原子切换和 Claude user-scope `remove → add → get` 注册事务。失败时恢复旧部署配置和 Claude 用户配置。设置工具不得下载依赖、运行 npm/pnpm/npx、请求 UAC、修改项目文件或自动处理登录秘密。

## 结果

用户可以在安装后自行选择便利性、可见性和身份隔离，不需要重装。保存用户令牌会扩大同一 Windows 用户下的持久连接权限；独立 Profile 提供更清晰的登录态边界，但需要在该 Profile 中单独完成登录，并且无头模式不适合首次 MFA/验证码流程。
