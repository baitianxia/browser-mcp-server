# 部署与运维

状态：当前规范；适用于 `browser-mcp-server` Windows x64 用户包。

## 发布方构建

发布只面向用户交付一个 ZIP：

```text
browser-mcp-server-<版本>-windows-x64.zip
```

ZIP 外附 `<ZIP>.sha256`，解压后只有一个顶层目录。`tar.gz` 运行包、测试夹具和发布门禁日志只属于内部流水线制品。

在可联网的隔离 Windows x64 构建机使用 Windows PowerShell 5.1：

1. 固定 Node.js 24.19.0、pnpm 11.19.0 和 Python 3.10+。从官方 Windows x64 ZIP 与同版本 `SHASUMS256.txt` 生成最小 Node 目录，并用 `config/windows-node-sources.json` 和 `validate_node_distribution.py` 校验来源、哈希、版本和 AMD64 PE。
2. 从 `config/playwright-extension-source.json` 下载精确批准的 Playwright Extension CRX，用 `validate_playwright_extension.py` 校验 ID、版本、哈希和清单。
3. 在原生 Windows 上运行：

   ```powershell
   powershell.exe -NoLogo -NoProfile -File .\scripts\build-offline-bundle.ps1 `
     -Profile core -NodeDistribution <批准的 Node 目录> -OutputDir .\dist
   python .\scripts\build-transfer-kit.py `
     --runtime-archive .\dist\browser-agent-runtime-<版本>-core-windows-x64.tar.gz `
     --extension-crx <批准的 CRX> --output-dir .\dist
   ```

4. 用 `verify-bundle.py`、`validate_windows_release_metadata.py` 和企业 SCA/恶意代码扫描复验 ZIP；包内必须包含 `NOTICE.md` 所述的许可证/第三方声明。
5. 在同一受控 Windows PowerShell 5.1 环境执行发布门禁：

   ```powershell
   powershell.exe -NoLogo -NoProfile -File .\scripts\verify-windows-release.ps1 `
     -TransferPath .\dist\browser-mcp-server-<版本>-windows-x64.zip `
     -ClaudeExecutable <已存在且可用的 claude.exe> `
     -LogPath .\windows-publisher-release-gate.log
   ```

门禁先验证 ZIP 外层和单顶层目录，再解析源代码与包内 PowerShell，运行完整 Python 测试、注册器自检、内层运行时验证、包内 `node.exe --version`、MCP stdio smoke 和隔离 Claude user-scope `remove/add/get`。它从仓库侧脚本运行，不能放进用户包，也不能由目标安装器调用。门禁执行前后都要验证包没有变化。

GitHub Actions 的 `.github/workflows/windows-release.yml` 在 `windows-2022` 与 `windows-latest` 上运行脚本解析和完整测试，在原生构建 job 生成并再次验证同一个 ZIP，最后以 `archive: false` 上传 ZIP 和相邻校验文件。发布流水线不得把门禁通过的 ZIP重新压缩成第二个用户包。

## 用户安装边界

用户解压 ZIP 后只双击顶层 `INSTALL.cmd`。安装器复用当前用户已有的原生 `claude.exe` 或 npm `claude.cmd`，并在可验证的情况下将 npm 入口转换为直接 `node.exe + 已安装 cli.js`；它不运行 npm、pnpm、npx，不下载依赖，也不修改系统 Node.js。缺少可用 Claude Code 时，在任何真实配置变更前停止。

安装根目录固定为 `%USERPROFILE%\browser-mcp-server`，配置根目录为 `%USERPROFILE%\browser-mcp-server\config`，设置文件为 `settings.json`。运行时版本放在该根目录的版本子目录，发布和回滚使用同卷暂存、清单校验、原子目录移动和有界退避；外部锁定时保留旧目录，不强行删除或请求 UAC。安装、升级、配置和卸载只读取或修改本工程根目录以及 Claude Code 当前用户的 `browser-mcp` 条目。

Chrome/Edge 的 `LOCALAPPDATA` 只用于发现浏览器既有 Profile 和检查扩展安装；本工程运行时、配置、输出、扩展副本、维护脚本和备份均在 `%USERPROFILE%\browser-mcp-server` 下。不得创建共享 `ClaudeTools` 根目录，也不得清理其他工程的路径或 MCP 条目。

扩展安装优先使用当前用户的离线策略；策略不可用时立即降级为人工加载，并在同一安装进程等待检测。自动打开扩展页和复制路径属于便利步骤，失败不应破坏安装事务。人工加载的目录必须是包内批准 CRX 解出的 `unpacked` 目录，安装器要再次核对 ID、版本和逐文件哈希。

扩展复用规则由 `docs/architecture.md` 和批准文件的 `compatibleVersions` 定义。当前列表只有 `0.4.0`；其他版本尚未获准复用。以后经真实 MCP 连接验证批准的兼容版本可选择保留或更新，无法显示选择时默认保留。未批准或不可用的扩展必须修复；用户选择更新后，旧兼容版本不能被当作更新成功。安装摘要记录实际复用的版本。发布者新增兼容版本时须同步批准记录、两级元数据及验证证据。

## 配置与重载

用户可运行 `%USERPROFILE%\browser-mcp-server\CONFIGURE.cmd`，或编辑 `config/settings.json` 后调用 MCP 工具：

- `browser_config_status` 返回产品身份、绝对配置路径、schema 版本、当前模式、缺失字段和下一步命令，秘密保持遮罩。
- `browser_configure` 只接受 schema 允许的非秘密字段，使用临时文件和原子替换。
- `browser_config_reload` 重新读取设置；快照、兼容性和等待时间可在线更新，浏览器启动方式、channel 或可执行文件变化需要重启 MCP/Claude Code。

扩展令牌只在注册事务的子进程环境中短暂存在，并保存在 Claude Code user-scope 配置；它不得出现在 `settings.json`、部署清单、命令行日志、测试输出、ZIP 或 MCP 响应。日志写入 `%TEMP%\browser-mcp-server\`，报告前删除令牌、Cookie 和个人路径。

## 升级、回滚和卸载

升级步骤：解压新 ZIP、确认顶层目录、双击新版 `INSTALL.cmd`。安装器只从当前 `browser-mcp-server` 清单提取受支持的浏览器模式、channel、授权、无头和交互设置，不复制旧运行时、输出或任意路径；历史旧身份和旧共享目录不迁移。新版本验证完成后再原子切换，失败恢复旧配置、旧 Claude 字节和旧版本目录。

回滚时停止 Claude Code，使用同一批次备份恢复配置和 user-scope 注册，再运行 `browser_config_status` 与一次只读 MCP smoke。不要只恢复配置的一侧，也不要删除仍可能被进程使用的目录。`UNINSTALL.cmd` 只移除当前 `browser-mcp` 注册和本工程活动目录，默认保留配置备份；其他工程目录、MCP 名称和共享路径不在卸载范围。

## 发布与企业验收未覆盖项

本机开发测试不能替代原生 Windows x64/PowerShell 5.1 门禁。企业 EDR、组策略、代理/证书、应用白名单、SSO/MFA、真实业务页面、签名、制品库导入和生产审批必须在企业环境单独验收。没有这些证据时，包只能标为候选或未验证，不能标为正式交付。
