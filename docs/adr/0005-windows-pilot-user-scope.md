# ADR 0005：Windows 试点使用 Claude Code user scope

状态：已接受；2026-08-30 按 Windows pilot extension 决策修订。

## 背景

Windows 通用内网试点要让当前用户在任意 Claude Code 项目中使用同一个本地 Playwright MCP。项目目录不是 MCP 服务的运行前提；只有 project scope 才需要在项目根写 `.mcp.json`。此前向导固定创建 `C:\BrowserAgent\Workspace`，把机器级工具错误绑定到单个项目。

Claude Code 当前官方 scope 契约是：project scope 写项目根 `.mcp.json`，user scope 写当前用户配置并在该用户的所有项目加载。

## 决策

- Windows 通用内网 pilot 固定使用 Claude Code `user` scope。
- 运行时、配置、输出、离线 CRX 和已解压扩展副本全部放在 `%LOCALAPPDATA%\IntranetBrowserAgent`，不请求管理员权限。pilot 不创建专用浏览器 Profile，而是通过扩展连接人明确批准的现有 Tab。
- 向导使用 Claude Code CLI 注册绝对路径 stdio MCP；注册前备份用户配置，失败时恢复。
- 注册前允许删除同名旧条目；首次安装没有旧条目时继续执行 `mcp add`。Windows PowerShell 5.1 捕获原生命令 stderr 时不得把普通诊断误判为终止错误，最终结果以退出码为准。
- 向导不要求项目目录，也不写项目 `.mcp.json`、项目 `CLAUDE.md` 或固定工作区。
- `workspaceRoots=[]` 明确表示安装时不绑定项目；实际根目录由 Claude Code 会话及 MCP roots 协商。
- production 仍可按组织需要使用 project 或 managed scope，本决策只覆盖通用 Windows pilot。
- preflight 自动检查所有 user-scope 安装路径位于 `%LOCALAPPDATA%`，并确认 extension 模式、浏览器 channel、无 `userDataDir` 和人工连接批准；安装人员不需要回答这些可计算的配置事实。

## 结果

普通用户双击一次即可进入完整流程，工具对其全部 Claude Code 项目生效，且不会污染仓库或因 UAC 切换身份而把 MCP 注册到错误账号。浏览器拒绝包内 CRX 策略安装时，用户需在自动打开的扩展页加载一次屏幕给出的本地目录；原安装进程等待并继续，不要求重跑。代价是每个需要使用该 MCP 的 Windows 用户都要各自安装一次，并且 pilot 对被批准 Tab 中的现有登录身份具有操作能力。
