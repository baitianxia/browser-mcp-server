# ADR 0008：发布门禁与目标机单次安装分离

状态：已接受（2026-08-31）

## 背景

ADR 0006 曾要求 Windows 安装器在接触真实用户配置前运行假 Claude CLI 注册自检，并要求顶层 launcher 在安装前运行完整发布门禁。该设计把发布者应承担的单元测试、PowerShell AST 扫描和故障矩阵带到了离线目标机。1.0.11 在一台可正常使用 Claude Code 的目标机上因此失败：完整测试中的注册器子进程测试受 30 秒测试超时约束，超过时限后安装在任何功能验证前停止。GitHub Runner 上通过同一测试不能证明任意内网终端都能在该时限内完成开发测试。

## 决定

- `verify-windows-release.ps1` 是发布者门禁，只在受控 Windows x64 发布流水线运行。它继续执行完整 Python 测试、全部打包 PowerShell AST 解析、假 Claude CLI 注册故障矩阵、包内 Node/MCP 握手、真实 Claude CLI 隔离探针及测试前后制品完整性校验。
- 目标迁移包的 `INSTALL-WINDOWS-PILOT.cmd` 只启动一次 `INSTALL-WINDOWS-PILOT.ps1`，不得调用发布门禁、单元测试发现器或注册器 `self-test`。安装失败后的重试不应重复任何发布者测试。
- 目标安装器仍须自行完成与目标状态有关的确定性校验：迁移目录完整性、Windows/Python/现有 Claude Code、Windows x64 原生构建元数据、包内 Node 来源与版本、离线扩展、实际 MCP stdio 握手、暂存/原子发布、事务化真实 user-scope 注册及安装后 preflight。
- 一键安装只接受 `buildHost=windows/x64`、`target=windows/x64`、`crossBuilt=false` 且 `targetCliSmokeTested=true` 的正式制品。交叉构建候选不得把目标机当成发布验证机，安装器必须在持久化变更前拒绝它。
- Windows 流水线必须分别保存发布门禁日志和单次安装日志，并断言发布门禁实际运行注册故障自检、目标安装日志没有该自检标记。只有两者都通过才上传可交付迁移包。

## 后果

内网用户只需双击一次，不再承担发布测试耗时、测试框架超时或重复故障演练。发布缺陷会在生成可下载制品之前暴露；目标机仍会拒绝损坏、错误平台、未完成目标 CLI 验证或与本机现有 Claude Code 不兼容的实际安装。该决定取代 ADR 0006 中“安装器运行注册器自检”和“launcher 运行完整发布门禁”的要求；ADR 0006 的事务化注册与回滚要求、ADR 0007 的直接 Node/MCP 可执行链和发布加固要求继续有效。
