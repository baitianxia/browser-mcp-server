# Intranet Browser Agent

让 Claude Code 操作当前 Windows 用户已经登录的 Chrome 或 Edge 网页。

安装后会注册一个名为 `intranet-browser-agent` 的本地 MCP。它只在 Claude Code 使用时启动，不是 Windows 服务，也不监听网络端口。

## 可以做什么

- 打开和读取网页。
- 点击、输入、选择、查询和填写表单。
- 操作标签页、弹窗、键盘和鼠标。
- 截图并检查操作结果。
- 复用用户已登录网页中的 Cookie、SSO 和登录状态。
- 也可改用独立浏览器 Profile，不共享原有登录状态。
- 在当前 Windows 用户的所有 Claude Code 项目中使用。

它不配置网址白名单，可以访问目标机网络能够正常打开的内网或其他网页，但不会绕过防火墙、证书、网站权限、验证码或 MFA。

## 安装要求

- Windows x64。
- 当前用户已经可以正常使用 Claude Code。
- Chrome（优先）或 Edge。
- Python 3.10 或更高版本。

目标机可以完全断网。安装包已包含运行所需的 Node.js、Playwright MCP 和浏览器扩展，不需要升级系统 Node.js，也不会运行 npm、pnpm 或 npx。

## 安装

使用发布方提供的正式文件：

```text
intranet-browser-agent-transfer-<版本>-windows-x64-ready.zip
```

用户只需：

1. 解压 ZIP 一次。
2. 进入解压后的唯一目录。
3. 双击一次 `INSTALL-WINDOWS-PILOT.cmd`，等待安装成功。

不需要管理员权限，也不需要填写项目目录、网址、防火墙、浏览器 Profile 或审批单号。

### 如果提示手动加载浏览器扩展

保持安装窗口打开，按窗口提示：

1. 打开 `chrome://extensions` 或 `edge://extensions`。
2. 开启“开发者模式”，点击“加载已解压的扩展程序”。
3. 选择窗口显示的扩展目录。

加载成功后安装器会自动继续，不需要重新运行。

## 使用

1. 安装成功后重启 Claude Code。
2. 在任意项目中输入 `/mcp`。
3. 确认 `intranet-browser-agent` 已连接。
4. 提出浏览器任务；默认在 Playwright Extension 中选择要授权的已登录标签页。

第一次建议只做读取测试：

```text
使用 intranet-browser-agent 读取当前授权标签页的标题，不执行写操作。
```

也可以这样使用：

```text
打开内网 OA，列出前五条待办，不要修改内容。

在当前系统中查询订单 20260831001，告诉我订单状态。

填写查询条件并点击查询；提交、删除或审批前先向我确认。
```

默认是有头模式，用户可以看到并随时接管操作。MCP 只能访问用户明确授权的标签页，不会自动接管全部浏览器页面，也不会自动输入密码、验证码或 MFA 信息。

## 更改浏览器设置

安装后双击：

```text
%LOCALAPPDATA%\IntranetBrowserAgent\BROWSER-AGENT-SETTINGS.cmd
```

可以选择记住当前用户的扩展授权（首次复制一次令牌）、恢复每次连接确认，或使用不共享原 Chrome/Edge 登录态的独立 Profile。独立 Profile 可选有头或无头；首次登录建议先用有头模式。保存后重启 Claude Code 即可，不需要保留安装包或重新安装。

## 安装包还需要保留吗

安装成功并确认 `/mcp` 已连接后：

- ZIP 和解压目录都可以删除。
- 不要删除 `%LOCALAPPDATA%\IntranetBrowserAgent`。
- 不要删除浏览器中的 Playwright Extension。

如果以后需要离线重装，可以在内网软件库保留一份 ZIP。

## 安装失败

不要运行 npm、pnpm 或 npx 修复命令。直接提供安装日志：

```text
%TEMP%\IntranetBrowserAgent\INSTALL-WINDOWS-PILOT-*.log
```

如有问题，请联系 tianxiabai。
