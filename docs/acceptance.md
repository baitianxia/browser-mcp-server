# 验收标准

状态：当前、规范性文档。

正式交付物是 `browser-mcp-server-<版本>-windows-x64.zip`。它只能在原生 Windows x64、Windows PowerShell 5.1 门禁和企业发布流程全部通过后标为正式包；失败或交叉构建结果只能标为候选。

## 自动验收

- `python3 -m unittest discover -s tests -v` 全部通过；测试覆盖兼容层 JSON-RPC、配置状态/写入/重载、动态页面回退、artifact containment、注册事务和失败回滚。
- `scripts/verify-bundle.py` 对运行包、公共 ZIP 和各自解压目录均返回 `VALID`。包内 `SHA256SUMS.txt` 和 ZIP 相邻 `.sha256` 使用规范小写 SHA-256、两个空格、文件名和 LF，无 BOM/CRLF。
- `validate_windows_release_metadata.py` 证明 `product=browser-mcp-server`、`displayName=浏览器助手`、`mcpServerName=browser-mcp`、`target=windows/x64`、`buildHost=windows/x64`、`crossBuilt=false`、`targetCliSmokeTested=true` 和 `bundledNode=true`。版本、运行包哈希/大小、Node 来源和扩展 ID/版本必须可追溯。
- 公共 ZIP 只有一个顶层目录，并在顶层包含 `README.md`、`START-HERE.html`、`INSTALL.cmd`、`CONFIGURE.cmd`、`OPEN-CONFIG.cmd`、`UNINSTALL.cmd`、`config/settings.example.json`、`payload/`、`release-manifest.json`、`SHA256SUMS.txt` 和 `NOTICE.md`。用户 artifact 直接上传该 ZIP 和相邻 checksum，不能再由流水线重新压缩。
- 公共 ZIP 不得包含 `.git`、测试/夹具、Python bytecode、缓存、秘密、npm/pnpm/npx/corepack、symlink/junction/reparse point、非 Windows x64 二进制或发布门禁脚本。`core` 运行时不得含 `chrome-devtools-mcp`。
- 最小 Node 目录只包含批准的 `node.exe`、`LICENSE`、`VERSION`、`SOURCE.json`；逐文件哈希、官方来源、AMD64 PE 和实际版本均通过。
- MCP 配置是 user-scope stdio，命令直接执行包内已验证 `node.exe` 和固定兼容层；不得使用 `.cmd`、`.bat`、`npx`、`@latest`、HTTP endpoint 或秘密。环境映射必须与 `config/windows-mcp-environment.json` 完全一致；只有明确选择持久扩展授权时才允许额外的格式合法 `PLAYWRIGHT_MCP_EXTENSION_TOKEN`。
- 配置和路径契约固定为 `%USERPROFILE%\browser-mcp-server\config\settings.json` 与 `%USERPROFILE%\browser-mcp-server`。运行时、扩展、输出、维护和备份不得写入共享 `ClaudeTools` 或其他工程目录；Chrome/Edge 的 `LOCALAPPDATA` 仅用于浏览器 Profile/扩展发现。
- Windows CI 在 `windows-2022` 与 `windows-latest` 的 Windows PowerShell 5.1 上解析全部 `.ps1` 并运行完整 Python suite；原生 package job 运行 `scripts/verify-windows-release.ps1`，再执行一次顶层 `INSTALL.cmd` 和设置工具 smoke。目标 launcher 日志不得包含测试发现器、AST 扫描或注册器 `self-test`。
- 发布门禁必须先验证 ZIP/解压目录，再执行 PowerShell AST、完整测试、注册器自测、内层运行包和包内 Node `--version`、MCP `initialize/tools/list`，并用临时 `CLAUDE_CONFIG_DIR` 对真实现有 Claude CLI 做隔离 `remove/add/get`。门禁执行前后复验包未改变，且不能由目标 launcher 调用。
- 安装器必须在真实写入前验证现有 Claude Code；原生 `claude.exe` 和可验证的 npm `claude.cmd` 均支持，缺少入口时停止且不安装 Claude。注册失败必须逐字节恢复 Claude 用户配置和旧部署配置。
- 安装、升级、设置和卸载只影响当前工程。升级从当前受管清单提取浏览器模式、channel、授权、无头和交互设置，不复制旧运行路径、未知字段或历史身份；新版本验证完成后才原子切换。`UNINSTALL.cmd` 只移除 `browser-mcp` 和本工程活动目录，默认保留配置备份。
- 扩展必须是批准 CRX 与逐文件一致的 `payload/browser-extension/unpacked`。自动策略不可用时必须显示三步人工加载指引并在同一进程等待，检测到精确 ID/版本/路径后续跑；不得下载扩展或接受任意目录。
- 初始配置必须是 `extension + headed + session approval + compact + robust`，Extension 不得有 `headless/userDataDir`；persistent 只能使用本工程独立 Profile，并显式记录 headed/headless。配置工具可分别切换 `compact|full`、`robust|standard`，启动方式变化要求重启 MCP。
- 所有日志和 MCP 响应不得包含扩展令牌、Cookie、密码或完整个人路径；令牌只通过短期进程环境进入注册事务，并持久化在 Claude user-scope 条目。

## 原生 Windows 门禁

发布方在 Windows x64、Windows PowerShell 5.1 Desktop 上执行：

```powershell
powershell.exe -NoLogo -NoProfile -File .\scripts\verify-windows-release.ps1 `
  -TransferPath .\dist\browser-mcp-server-<版本>-windows-x64.zip `
  -ClaudeExecutable <现有 claude.exe>
```

门禁通过后，流水线才可上传同一个 ZIP。当前开发主机没有原生 Windows/PowerShell 5.1，因此本地通过的 Python 和静态检查不能替代这一步；未运行门禁时不得声称 Windows 已验收。

## 人工验收

1. 在目标 Chrome/Edge 完成 SSO/MFA，只批准本次所需标签页，先执行读取标题的只读任务。
2. 验证导航、SPA/AJAX 更新和动态控件使用新快照；删除、上传、提交和权限变更在最终动作前请求确认。
3. 验证 Extension 只看到已批准的现有 Profile；切换 persistent 后只使用 `browser-mcp-server\browser-profile\pilot`，不复用日常 Cookie。
4. 验证截图、下载、PDF 和视频 artifact 都位于配置 output 目录，并返回可读绝对路径；HTTP clipboard 页面应给出 secure-context 诊断。
5. 在企业 EDR、组策略、代理/证书、应用白名单和真实业务页面环境中复核安装、升级、失败回滚和卸载。
6. 发布方保留构建日志、SBOM/SCA、签名、哈希、批准记录和门禁日志；不得把令牌或个人配置上传为证据。

## 退出条件

缺少原生 Windows 门禁、许可证/来源追溯、独立网络与身份边界、可回滚配置事务或人工高风险确认时，停止生产推广。企业签名、SCA、SSO/MFA 和生产审批属于外部流程，不能由项目文档虚构为已通过。
