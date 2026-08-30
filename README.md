# 内网 Browser Agent 落地套件

[![Windows release validation](https://github.com/baitianxia/intranet-browser-agent/actions/workflows/windows-release.yml/badge.svg)](https://github.com/baitianxia/intranet-browser-agent/actions/workflows/windows-release.yml)

这是一套面向 Claude Code + Playwright MCP 的可审计、可离线交付基线。它不是另造一个浏览器 Agent；它把浏览器接入、部署前置条件、安全门禁、配置生成和验收固化成代码。

只做 Windows 内网测试时，拿到已校验并解压的目录后只需双击一次 `INSTALL-WINDOWS-PILOT.cmd`；自动门禁和安装会在同一个窗口连续完成。最短说明见 [`docs/windows-quickstart.md`](docs/windows-quickstart.md)，`docs/operations.md` 是生产、回滚和故障处理用的完整手册。

默认生产拓扑是：

```text
Claude Code ──stdio──> Playwright MCP ──local──> 企业 Chrome
                                                └─ 专用持久化 Profile
```

核心选择：

- 默认 `persistent` 模式：独立 Chrome Profile，人完成 SSO/MFA，Agent 在认证后工作。
- `extension` 模式：Windows 通用内网 pilot 固定采用此模式来连接用户批准的现有 Tab；生产仅用于已经能通过 Chrome Enterprise 管理扩展的终端。两者都默认保留每次连接的人为批准。
- `cdp` 模式：只允许 Chrome channel 或 loopback endpoint，作为兼容性回退，不作为隔离边界。
- MCP 只使用本机 `stdio`；不生成监听 `0.0.0.0` 的服务配置。
- Playwright 的 origin 过滤只作为可选防误操作护栏；Windows 通用内网试点不生成该白名单，默认允许浏览当前网络可访问的全部网址。生产如需限制范围，仍应使用企业代理、主机防火墙或隔离 VDI 网络。
- `CLAUDE.md` 规则只约束 Agent 行为，不替代网络、文件系统、身份和审批控制。

## 交付内容

- `tools/browser_agent.py`：部署清单校验、配置渲染、运行前检查。
- `schema/deployment.schema.json`：部署清单结构契约。
- `config/deployment.windows-pilot.json.template`：Windows x64 内网试点模板，默认失败关闭。
- `config/deployment.windows-production.json.template`：Windows x64 生产模板，默认失败关闭。
- `config/windows-mcp-environment.json`：Windows MCP 子进程的固定环境覆盖策略，阻断调用者遗留变量改写包内配置。
- `config/windows-node-sources.json`：当前 Windows 发布批准的官方 Node 来源固定哈希。
- `config/deployment.pilot.json.template`：Linux x64 试点模板。
- `config/deployment.production.json.template`：故意保持未批准状态的生产模板。
- `config/deployment.local-demo.json`：仅供本地回环演示的可渲染样例。
- `templates/CLAUDE.browser.md`：Observe → Act → Re-observe → Verify 操作规则。
- `scripts/build-offline-bundle.sh`：在有网构建区生成固定版本、带校验清单与 CycloneDX 清单的离线运行包。
- `scripts/build-transfer-kit.py`：把已验证运行包和白名单部署工具链组装成一个内网迁移包。
- `scripts/verify-bundle.py`：在内网安装前验证文件完整性。
- `.github/workflows/windows-release.yml`：在 GitHub 托管的 Windows x64、Windows PowerShell 5.1 上运行回归，并原生构建、门禁和安装迁移包。
- `INSTALL-WINDOWS-PILOT.cmd`：迁移包顶层的 Windows 试点双击安装入口。
- `scripts/configure_windows_pilot.py`：向导调用的配置生成与 `%LOCALAPPDATA%` 路径/link 检查工具。
- `docs/windows-quickstart.md`：Windows x64 内网试点的一页式操作入口。
- `docs/`：评审、架构、威胁模型、完整运维和验收标准。

## 快速验证

源码工具要求 Python 3.10+；构建离线运行包另需 Node.js 20.19+ 和 pnpm 11.19.0。当前 Windows x64 试点迁移包自带最小 Node.js 运行时，目标机不需要安装或升级系统 Node.js。

```bash
python3 -m unittest discover -s tests -v

python3 tools/browser_agent.py validate \
  --manifest config/deployment.local-demo.json

python3 tools/browser_agent.py render \
  --manifest config/deployment.local-demo.json \
  --out build/local-demo
```

推送到 GitHub 后，`Windows release validation` 会在 `windows-2022` 和 `windows-latest` 上用 Windows PowerShell 5.1 跑完整测试；随后在 `windows-latest` 原生构建 Windows 运行包，安装固定 Claude Code 2.1.84，并从解压后的迁移包执行顶层 `INSTALL-WINDOWS-PILOT.cmd`。只有发布门禁、MCP 握手、隔离 user-scope 注册、完整安装和安装后校验均通过，才上传可下载的 Windows 迁移包 artifact。GitHub 有网构建阶段安装固定 pnpm，不会把 npm、pnpm 或 npx 带入迁移包；内网目标机仍禁止用包管理器修复。

Windows 内网测试不要直接使用 demo。正式放行仍要求发布方先在受控 Windows x64、Windows PowerShell 5.1 上运行 `scripts/verify-windows-release.ps1`。明确标记为“自检候选”的交叉构建包会把同一门禁强制串入顶层 `INSTALL-WINDOWS-PILOT.cmd`：一次双击先执行测试前后双重完整性校验、完整测试、全部 PowerShell 脚本 AST 解析、假 Claude CLI 失败回滚、内层运行时临时解压、包内 `node.exe` 来源/版本校验、真实 MCP stdio `initialize + tools/list` 握手，以及临时 `CLAUDE_CONFIG_DIR` 下的真实 Claude CLI 隔离探针；全部通过后才在同一窗口开始安装。向导不请求 UAC、不询问项目目录，会把固定运行时安装到当前用户 `%LOCALAPPDATA%`，自动识别 Chrome/Edge，并通过原生 Claude Code `claude.exe` 注册 user-scope MCP。迁移包已携带固定官方 CRX 和逐文件一致的已解压扩展：浏览器接受本地策略时自动安装；拒绝时向导自动打开扩展页、复制唯一目录并等待用户加载，成功后原进程续跑，无需第二次启动。实际 MCP 直接执行包内 `node.exe + 固定 cli.js`，不依赖 `.cmd` shell shim；固定环境映射会覆盖固定版本支持的 Playwright MCP 配置变量、`NODE_OPTIONS` 和 `NODE_PATH`，避免包内配置被调用者环境悄悄改写。安装后该用户的项目均可使用（同名的 local/project 配置按 Claude Code 自身优先级覆盖 user scope）；试点不要求网址白名单、防火墙配置、浏览器选择或审批单号。具体只看 `docs/windows-quickstart.md`。

Windows 生产部署则从 Windows 生产模板开始：

```powershell
Copy-Item .\config\deployment.windows-production.json.template C:\BrowserAgent\deployment.windows-production.json
notepad.exe C:\BrowserAgent\deployment.windows-production.json

py -3 .\tools\browser_agent.py validate --manifest C:\BrowserAgent\deployment.windows-production.json
py -3 .\tools\browser_agent.py render --manifest C:\BrowserAgent\deployment.windows-production.json --out C:\BrowserAgent\rendered-production
```

生产如选用 project scope，可把手工生成目录中的 `.mcp.json` 放到 Claude Code 项目根，并按组织规范处理 `CLAUDE.browser.md`。Windows 通用试点固定使用 user scope，不写任何项目文件。

## 离线包

Windows 生产制品应在有网 Windows x64 受控构建机执行：

```powershell
powershell.exe -NoProfile -File .\scripts\build-offline-bundle.ps1 -Profile core -NodeDistribution C:\Build\node-minimal -OutputDir .\dist
```

不要临时绕过 PowerShell 执行策略；使用组织签名脚本或批准策略。若当前只有 macOS/Linux 构建机，可为内网测试交叉组装一个明确标记为未经过 Windows 目标验证的候选包：

```bash
PNPM_BIN=/absolute/path/to/pnpm \
NODE_BIN=/absolute/path/to/node \
scripts/build-offline-bundle.sh --profile core --target windows-x64 --output-dir dist \
  --node-distribution /absolute/path/to/node-minimal
```

Windows 交叉构建仅允许 `core` 候选包，并强制携带由 `prepare_windows_node_distribution.py` 生成、与 `config/windows-node-sources.json` 批准哈希一致的 Windows x64 最小 Node distribution；它只含 `node.exe`、许可证、版本和来源记录，不含 npm、npx 或 corepack。交叉构建仍未在 Windows 执行目标 `node.exe`、Claude CLI、Chrome/Edge 和 preflight，因此不能作为生产制品。内网导入前：

```bash
python3 scripts/verify-bundle.py dist/browser-agent-runtime-*.tar.gz
```

运行包验证通过后，组装完整迁移包：

```bash
python3 scripts/build-transfer-kit.py \
  --runtime-archive /absolute/path/to/browser-agent-runtime.tar.gz \
  --extension-crx /absolute/path/to/playwright-extension-0.3.0.crx \
  --output-dir dist
```

也可执行 `make transfer-kit RUNTIME_ARCHIVE=/absolute/path/to/runtime.tar.gz EXTENSION_CRX=/absolute/path/to/playwright-extension-0.3.0.crx`。最终迁移 `dist/intranet-browser-agent-transfer-*.tar.gz` 及其相邻 `.sha256`；包内 `START-HERE.md` 是内网操作入口。迁移包不会收录真实部署清单、`.git`、构建缓存或已有 `build/`、`dist/`。

Windows V1 仅支持 x64 和安全的本机盘符路径，不支持 Windows ARM64、UNC 或 `%LOCALAPPDATA%` 以下的 link/junction。Windows 一键包固定 `bundledNode=true`，向导在完整性、批准来源和目标机版本校验后直接使用包内 Node.js，不探测或修改系统 Node。

Windows 内网试点先看 `docs/windows-quickstart.md`；完整部署、回滚和人工认证步骤见 `docs/operations.md`，生产放行条件见 `docs/acceptance.md`。
本次实际执行过的检查和仍待企业环境验证的项目见 `docs/verification.md`。

## 文档权威顺序

1. `docs/architecture.md`：本项目当前架构和不可变约束。
2. `schema/deployment.schema.json` 与 `tools/browser_agent.py`：机器可执行契约；二者冲突属于缺陷。
3. `docs/threat-model.md`：安全边界和残余风险。
4. `docs/operations.md`、`docs/acceptance.md`：部署运行和放行要求。
5. `docs/design-review.md`、`docs/adr/`：评审依据和已接受决策。
