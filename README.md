# 浏览器助手（browser-mcp-server）

`browser-mcp-server` 是一个离线、用户范围的本地 MCP，显示名为“浏览器助手”，在 Claude Code 中以 `browser-mcp` 出现。它通过本机 stdio 连接固定版本的 Playwright MCP，帮助用户操作自己明确授权的 Chrome 或 Edge 标签页。

安装包不启动 Windows 服务、不监听网络端口、不修改项目 `.mcp.json` 或 `CLAUDE.md`，也不会安装、升级或修复 Claude Code。网页仍受浏览器登录、扩展授权、站点权限、验证码和 MFA 约束；删除、上传、提交、权限变更和对外沟通等高风险动作由 Claude Code 的确认流程处理。

## Windows x64 离线安装

发布方交付的文件是单个 ZIP：

```text
browser-mcp-server-<版本>-windows-x64.zip
```

ZIP 只有一个顶层目录，目录内包含 `README.md`、`START-HERE.html`、`INSTALL.cmd`、`CONFIGURE.cmd`、`OPEN-CONFIG.cmd`、`UNINSTALL.cmd`、`config/settings.example.json`、`payload/`、`release-manifest.json` 和 `SHA256SUMS.txt`。发布方应先验证 `SHA256SUMS.txt`，再把 ZIP 解压一次。

进入唯一目录后只需双击一次 `INSTALL.cmd`。它同时覆盖首次安装和升级：运行时、配置和 Claude Code user-scope 条目会在同一事务中暂存、预检、发布；失败会恢复旧配置并保留带时间戳的备份。安装目录固定在：

```text
%USERPROFILE%\browser-mcp-server
```

设置文件固定在：

```text
%USERPROFILE%\browser-mcp-server\config\settings.json
```

不需要管理员权限、项目目录、网址白名单或系统 Node.js。包内携带经过批准的 Windows x64 Node.js 和 Playwright 运行时。浏览器扩展如果不能由当前用户策略自动安装，安装器会打开扩展页并等待用户加载包内的已解压目录；同一个安装进程会自动继续。

已启用的当前扩展直接复用；其他版本只有进入发布方兼容批准列表后才可选择保留，否则进入离线修复。当前批准列表仅包含 `0.4.0`。

安装后重启 Claude Code，在任意项目运行 `/mcp`，应看到 `browser-mcp`（浏览器助手）。第一次调用建议只读取一个已授权标签页的标题。

## 设置、状态和重载

双击 `%USERPROFILE%\browser-mcp-server\CONFIGURE.cmd`，或使用包目录中的 `CONFIGURE.cmd`。它会调用已安装的设置工具，不会改写项目文件。也可以双击 `OPEN-CONFIG.cmd` 直接打开 `config/settings.json`。

MCP 本身提供三个配置工具：

- `browser_config_status`：显示产品身份、设置路径、当前模式、缺失字段和下一步命令；令牌等秘密会被遮蔽。
- `browser_configure`：只接受经过校验的非秘密字段，以临时文件和原子替换写入设置文件。
- `browser_config_reload`：重新读取磁盘设置并应用快照/动态页面兼容选项；浏览器启动方式或可执行文件变化时重启 Claude Code MCP 进程。

扩展令牌只能暂时通过注册器进程环境传递，并且唯一持久化在 Claude Code 当前用户配置中。它不会写入 ZIP、`settings.json`、部署清单、项目文件、日志或 MCP 响应。

## 开发与验证

本仓库保留离线构建、清单渲染、运行包完整性和 Windows 发布门禁脚本。常用检查：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/verify-bundle.py <bundle-or-extracted-directory>
```

Windows 正式包必须在原生 Windows x64 构建机生成，并由 `scripts/validate_windows_release_metadata.py`、`scripts/verify-bundle.py` 和发布流水线复验。当前开发主机未执行真实 Windows x64 安装验收时，不能把本地结果描述为 Windows 已验收。
