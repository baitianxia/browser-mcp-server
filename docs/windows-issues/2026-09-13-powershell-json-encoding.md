# Windows PowerShell 5.1 读取发布清单编码失败

## 基本信息

- 项目：`browser-mcp-server`
- 发现日期：2026-09-13
- 严重程度：阻塞
- 适用环境：用户反馈的 Windows x64、Windows PowerShell 5.1、`browser-mcp-server-1.0.16-windows-x64.zip`
- 记录状态：根因和修复已确认；目标用户机器的 Windows 版本与活动代码页未采集

## 症状

用户执行 1.0.16 顶层安装器时，在验证迁移包阶段失败。日志中的 `displayName` 已经出现乱码，随后 `ConvertFrom-Json` 报 JSON 语法错误。失败发生在安装器读取发布元数据之后，尚未进入运行时修复或依赖安装阶段。

脱敏后的关键日志如下：

```text
STEP 1/6: 检查环境并验证迁移包
PYTHON: WINDOWS RELEASE METADATA: VALID
FAILED: 传入的对象无效，应为“:”或“}”。 (331)
  "displayName": "娴忚鍣ㄥ姪鎵?",
STACK: 在 <ScriptBlock>、...\payload\INSTALL-WINDOWS-PILOT.ps1 中: 第 941 行
```

## 复现与证据

1. 发布构建器用 UTF-8（无 BOM）写入 `release-manifest.json`，其中 `displayName` 是中文；该文件的字节内容和包内校验均正确。
2. 1.0.16 安装器使用下面的隐式读取方式：

   ```powershell
   Get-Content -LiteralPath $ReleaseManifestPath -Raw | ConvertFrom-Json
   ```

3. Windows PowerShell 5.1 在无 BOM 文本上会按活动系统 ANSI 编码读取 `Get-Content`。Microsoft 的字符编码说明明确记录了这一行为；因此读取结果依赖目标机代码页，不能满足发布清单的 UTF-8 契约。
4. 用户日志同时给出了乱码字段和解析失败，证明失败发生在解码/解析边界。我们没有取得该机器的活动代码页，也没有在同一台中文 Windows 上重新执行完整失败，所以不把“代码页 936”或具体解析器错误形态写成已测事实。

参考：[Microsoft PowerShell 字符编码说明](https://learn.microsoft.com/zh-cn/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.6)。

## 根因

已确认的根因是：发布文件的写入契约是 UTF-8，而 Windows PowerShell 5.1 的读取代码没有指定编码，隐式采用了目标机的 ANSI 代码页。`PYTHON: WINDOWS RELEASE METADATA: VALID` 只证明 Python 校验器能正确读取文件，不能证明安装器的 PowerShell 读取路径也正确。

这不是 npm、pnpm、npx、Claude Code 或网络修复问题；错误发生在安装器解析本地元数据的最前段。修改 ZIP 内文件或在内网目标机运行包管理器修复都会绕开完整性边界，不能作为处理方式。

## 修复

1. 在 `INSTALL-WINDOWS-PILOT.ps1`、`verify-windows-release.ps1` 和 `BROWSER-AGENT-SETTINGS.ps1` 中统一使用 `Read-Utf8JsonFile`。
2. 该 helper 使用 `UTF8Encoding($false, $true)` 和 `File.ReadAllText(path, encoding)`，遇到无效 UTF-8 时显式失败并保留文件路径。
3. 版本提升到 1.0.17，重新生成完整 ZIP 和相邻 SHA-256 sidecar；用户应下载新的完整包，不应在旧 ZIP 上打补丁。

`File.ReadAllText` 的带编码重载是按指定编码读取文件；.NET 同时会识别带 BOM 的 Unicode 文件。当前发布契约仍要求清单和 sidecar 使用规范 UTF-8/LF/无 BOM，若未来要拒绝 UTF-16/32 BOM，需要另加原始字节级校验，不能仅由该 helper 推断。参考：[File.ReadAllText](https://learn.microsoft.com/en-us/dotnet/api/system.io.file.readalltext?view=netframework-4.8.1) 和 [UTF8Encoding 构造函数](https://learn.microsoft.com/en-us/dotnet/api/system.text.utf8encoding.-ctor?view=netframework-4.8.1)。

## 验证结果

- [x] 本地 Python 回归：`Ran 167 tests`，其中 164 项通过、3 项按设计跳过。
- [x] 本地静态回归：确认三个 PowerShell 入口声明 strict UTF-8 helper，并且不再使用旧的隐式发布清单读取表达式。
- [x] 原生 Windows 发布门禁：[Actions run #88](https://github.com/baitianxia/browser-mcp-server/actions/runs/34755010110)，提交 `107d21bf3fd957f426da9e28f9ec5b5783143366`，Windows PowerShell 5.1 两套 job 和 package job 全部成功。
- [x] 1.0.17 用户包下载后 ZIP 与解压目录均为 `VALID`，Windows release metadata 为 `VALID`；ZIP SHA-256：`20d94e9b7bf91a82a1598c2c6cfadc4a8e40482b998b3d83b68b43a265be6cdd`。
- [ ] 在用户的中文 Windows 上采集实际 Windows 版本、PowerShell 版本和活动 ANSI/OEM 代码页。
- [ ] 在 Windows PowerShell 5.1 原生进程中，用无 BOM UTF-8 中文 fixture 动态执行安装器读取 helper，并对错误 UTF-8 做失败断言。当前测试是源码静态保护，不是这两项动态测试。

## 可复用经验

- “校验器显示 `VALID`”不能代表每个后续读取器都遵守同一编码契约；必须沿真实安装路径验证最终消费者。
- Windows PowerShell 5.1 的文件读取、脚本源码、原生命令输出和协议文件是不同边界，应分别声明编码，不能用 `chcp`、`$OutputEncoding` 或给脚本加 BOM 代替 JSON 读取修复。
- 发布故障记录必须保存原始字节/是否有 BOM、读取器、PowerShell 版本、活动代码页、完整包版本和 SHA-256；没有这些信息时，把目标代码页和精确错误形态标为待确认。
- 修复后应发布新版本并复验同一个 ZIP 的清单和哈希；不要让用户在内网运行 npm/pnpm/npx 或在线下载来补齐安装。

## 是否回写共用指南

已回写 [`docs/windows-development.md`](../../../docs/windows-development.md) 的“Windows PowerShell 5.1 读取 UTF-8 JSON”案例，并扩充 [`docs/windows-issue-template.md`](../../../docs/windows-issue-template.md) 的编码证据与验收字段。
