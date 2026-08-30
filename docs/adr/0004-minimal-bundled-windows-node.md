# ADR-0004：Windows 包携带最小 Node.js 运行时

状态：已接受；ADR-0007 将 Windows 一键包从“可以携带”收紧为“必须携带”，并增加当前发布批准哈希。

## 背景

试点目标机可能已有低于 MCP 要求的 Node.js，且内网主机不应运行 npm、pnpm、npx 或在线修复命令。强制运维先升级系统 Node 会增加部署步骤、变更范围和失败点。直接把官方完整 ZIP 放入运行包又会同时带入 npm、npx、corepack、安装脚本和数千个不需要的文件。

## 决策

Windows x64 运行包可以携带独立的最小 Node distribution。构建区必须先从 Node.js 官方 release 目录取得 Windows x64 ZIP 和同版本 `SHASUMS256.txt`，由 `prepare_windows_node_distribution.py` 验证 ZIP 哈希和 `win-x64/node.exe` 哈希后，只提取：

- `node.exe`
- `LICENSE`
- `VERSION`
- `SOURCE.json`

`validate_node_distribution.py` 对这四个文件执行精确白名单、逐文件 SHA-256、版本下限、官方 HTTPS URL、AMD64 PE machine 和 `config/windows-node-sources.json` 当前发布批准哈希校验。出现未批准来源、额外目录或 npm/npx/corepack 文件时失败。交叉构建只用构建主机 Node 对 JavaScript CLI 做冒烟，不执行目标 `node.exe`，所以仍记录 `targetCliSmokeTested=false`。

Windows 安装向导先校验外层迁移目录和内层运行 archive，再解压并校验最小 Node distribution，最后才在目标 Windows 上执行包内 `node.exe --version`。包内版本优先于系统版本；不会安装、升级或修改系统 Node，也不会执行任何包管理器修复命令。

## 结果

目标机可以保留旧版或不安装系统 Node，双击向导仍可离线完成。代价是迁移包增大约一个 `node.exe` 的体积，且交叉候选包仍必须在 Windows 上完成目标 `node.exe + cli.js`、Claude CLI、浏览器、路径/ACL 和 preflight 验收后才能进入生产。

Windows 生产制品仍必须在受控 Windows x64 构建机原生构建。官方 ZIP、官方 checksum、企业扫描、签名和批准记录继续由组织制品流程保留。
