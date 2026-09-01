# ADR-0003：Windows x64 与无链接运行时

状态：已接受；“交叉包不携带 Node”的限制已由 ADR-0004 取代，“`.cmd` 作为 MCP 命令”的部分已由 ADR-0007 取代。

## 背景

原运行包由 macOS 上的 pnpm 默认 isolated 布局生成，包含 POSIX symlink 和 shell wrapper；部署清单也没有声明目标 OS/架构。把该制品改名或直接解压到 Windows 不能形成可靠交付。pnpm 11 还要求把 `nodeLinker` 等项目设置放入 `pnpm-workspace.yaml`，不能依赖 `.npmrc` 中的同名设置。

## 决策

V1 明确支持 Windows x64。部署清单必须声明 `target.os` 与 `target.arch`，Windows 路径只允许本机盘符绝对路径，不接受 UNC。运行包保留 `.cmd` 诊断入口；ADR-0007 将实际 MCP 命令收紧为直接 `node.exe + 固定 JavaScript 入口`，ADR-0010 将该入口更新为同包兼容层。依赖安装固定采用 `nodeLinker: hoisted` 和 `packageImportMethod: copy`，删除 pnpm 元数据并拒绝 symlink、junction、reparse point 和原生二进制后再归档。

Windows 原生受控构建使用 `scripts/build-offline-bundle.ps1`。非 Windows 主机只可交叉组装 `core` 试点候选包，构建元数据必须标记 `crossBuilt=true` 和 `targetCliSmokeTested=false`；完成 Windows 目标 `node.exe + bin\intranet-browser-agent-mcp.js + 同包上游 CLI`、Claude CLI、Chrome、路径/ACL 和 preflight 验收前不得作为生产制品。目标 Windows Node 的最小化携带规则见 ADR-0004。

## 结果

运行 archive 可在不创建链接的情况下导入 Windows，且配置生成器会输出 Windows 路径和直接可执行命令。代价是 Windows 需要独立模板、路径/ACL 验收和原生构建脚本；V1 不支持 Windows ARM64、UNC 部署根或把 macOS/Linux Node distribution 交叉打入 Windows 包。

## 依据

- [pnpm 11 配置文件规则](https://pnpm.io/settings)
- [pnpm `nodeLinker: hoisted` 与 `packageImportMethod: copy`](https://pnpm.io/settings/node-modules)
