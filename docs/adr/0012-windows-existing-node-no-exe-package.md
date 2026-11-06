# ADR-0012：Windows 包不携带 EXE，改用目标机已有的 Node.js

状态：已接受（2026-11-05；2026-11-06 修订：Node 下限由 20.18.3 放宽到 20.12.0）。取代 ADR-0004；修订 ADR-0007 中“Windows 一键包必须 `bundledNode=true`”和“Node 来源批准哈希”两条。ADR-0007 关于直接执行 `node.exe`（不经 `.cmd`）、固定兼容层入口、注册事务、固定环境映射和路径边界的约束继续有效。

## 背景

- 目标电脑可能禁止运行未经授权的 EXE，这与 `claude-workspace-client` 浏览器版面对的约束相同。1.0.21 及以前的 Windows 包在内层运行包中携带 `node/node.exe`，它是整个包里唯一的 PE 文件。
- 已知的一台目标电脑（`claude-workspace-client` 的无 EXE 探针，2026-11-03）使用 Node.js 20.18.3（nvm4w）。
- Windows 核心运行包的依赖声明：`@playwright/mcp` 为 `node >=18`，`playwright`/`playwright-core` 为 `node >=20`。原来的 20.19.0 下限只来自 `chrome-devtools-mcp`，而它只出现在 diagnostic profile，不进入 Windows 公共包。对兼容层和固定 Playwright 包做的静态扫描显示：Node 侧唯一晚于 20.0 的依赖是 `fs.Dirent.parentPath`（20.12.0 加入），Playwright MCP 的输出目录大小上限用它枚举文件。我们的配置启用了这个上限；在 20.0–20.11 上它被上游的 `try/catch` 吞掉而静默失效，输出目录会无限增长。20.12.0 到 20.18.3 之间没有发现其他依赖。这只是静态证据，运行证据以发布 CI 为准。
- 安装器本来就复用目标机已有的 Python 3.10+ 和 Claude Code，不安装也不修复它们。

## 决策

1. **包内不得有可执行映像。** Windows 内层运行包和公共 ZIP 不得包含 `.exe`、`.dll`、`.pdb`、`.sys`、`.node`、`.msi`、`.ocx`、`.cpl`、`.scr` 文件，也不得包含 PE 内容（按内容识别，改了扩展名的 EXE 同样拒绝）。`verify-bundle.py --reject-native-binaries` 在构建、组包、发布门禁和目标安装（公共包、内层运行包、暂存运行时、已发布运行时）各执行一次。`.cmd`、`.ps1`、`.py`、`.js` 等脚本不受影响。
2. **元数据。** `BUILD-METADATA.json` 记录 `bundledNode=false` 和 `nodeRuntime={"source":"system","minimumVersion":"20.12.0"}`；`validate_windows_release_metadata.py` 要求这两项完全一致。`write_build_metadata.py` 和 `build-offline-bundle.sh` 拒绝为 Windows 目标携带 Node；POSIX 运行包仍可携带 Node。
3. **Node 下限。** 核心 profile 为 20.12.0：它是提供运行包全部 Node 侧 API 的最低 Node.js 20 版本，发布 CI 也在这个版本上实际运行（见第 7 条）。最初定为已知目标机的 20.18.3，2026-11-06 按“主版本 20 即可”的要求放宽；没有直接放到 20.0.0，是因为 20.0–20.11 会让输出目录大小上限静默失效（见背景）。diagnostic profile（含 `chrome-devtools-mcp`）保持 20.19.0。唯一来源是 `tools/browser_agent.py` 的 `MIN_NODE_VERSION`；验证器常量、`runtime/package.json` 的 `engines.node`、兼容层启动守卫、迁移包说明文字的兜底值和 CI 的 `FLOOR_NODE_VERSION` 由测试保证一致。只有 CI 在新版本上跑通，才能调整下限。
4. **选择 Node。** 安装器在第 1 步（任何持久化变更之前）调用公共函数 `Resolve-SystemNodeRuntime`（`scripts/windows-tool-discovery.ps1`）。候选顺序：PATH 中的 `node.exe`（按 PATH 顺序）、`%NVM_SYMLINK%\node.exe`、`%ProgramFiles%\nodejs\node.exe`。以下候选会被拒绝：不是本机盘符路径、文件名不是 `node.exe`、位于 `WindowsApps`、位于本工程目录 `%USERPROFILE%\browser-mcp-server` 内（例如旧版遗留的包内副本）、文件不存在、探测失败、不是 x64、版本低于下限。探测时临时清空 `NODE_OPTIONS`/`NODE_PATH`，执行 `node.exe -p "process.version+'/'+process.arch"`。每个拒绝原因写入安装日志；找不到可用 Node 时给出中文提示并停止。安装器不安装、升级、下载或修复 Node.js。运行时发布之后，安装器对同一路径再探测一次。
5. **MCP 注册。** `command` 仍是直接执行的 `node.exe` 绝对路径，只是不再位于运行包内；`args` 不变。记录的是候选路径本身（例如 nvm4w 的链接路径 `C:\nvm4w\nodejs\node.exe`），不解析为具体版本目录，这样 nvm 切换版本后条目仍可用；切换后的版本是否满足下限，由下面的启动守卫和设置工具复验发现。preflight 的 `windows-mcp-node-binding` 改为要求 `node.exe` 位于运行包之外；`user-scope-paths` 仍要求运行时、配置、输出和 Profile 位于 `USERPROFILE` 内，但不再约束 `node.exe`，因为已有的 Node.js 通常装在 Program Files 或 nvm 目录。
6. **运行期守卫。** 兼容层 `bin/intranet-browser-agent-mcp.js` 启动时检查 `process.versions.node`，低于下限时在 stderr 说明原因和下一步，并以退出码 1 结束。设置工具在重新配置前用同一解析器复验安装时登记的 Node。
7. **发布门禁与 CI。** `verify-windows-release.ps1 -NodeExecutable` 用与目标同版本的 Node（CI 中为 20.18.3，即 `CLAUDE_NPM_NODE_VERSION`）完成 MCP stdio 握手和隔离 Claude 注册。CI 一键安装后断言：已注册的 MCP `command` 就是这个 Node，已安装运行包内没有 `node` 目录。下限另由 `FLOOR_NODE_VERSION`（20.12.0，必须等于包内 `minimumVersion`）证明：CI 先用它运行兼容层协议测试（被跳过视为失败），安装完成后再用它对已安装运行包跑一次独立 Profile 真实浏览器 E2E。
8. 删除 `scripts/prepare_windows_node_distribution.py`、`scripts/validate_node_distribution.py`、`config/windows-node-sources.json` 及其测试。
9. 发布版本提升到 1.0.22，避免与已有 Windows 证据的 1.0.21（含 `node.exe`）同名。

## 结果

- 包内不再有 EXE。目标机前置条件新增“已有 Windows x64 Node.js 20.12.0 或更高版本”，与已有的 Python 3.10+、Claude Code、Chrome/Edge 并列。
- 信任边界变化：Node.js 不再由本工程用固定哈希校验，改由目标机的组织软件管理负责；本工程只校验版本、架构和路径类别。
- 系统 Node 被卸载、降级或改路径后，MCP 将无法启动。启动守卫和设置工具会给出明确提示；重新运行 `INSTALL.cmd` 会重新选择 Node。
- 低于 20.12.0 或 ARM64 的 Node 会被拒绝。Node.js 20 已于 2026-04-30 结束维护；下限只表示兼容，不表示安全，Node 的补丁和升级仍由目标机的软件管理负责。
- 目标机的 AppLocker/WDAC 或第三方安全软件是否允许已有的 `node.exe` 运行，由目标机策略决定；目前只有 `claude-workspace-client` 探针的单机证据。
- 2026-11-06，[Windows release validation #108](https://github.com/baitianxia/browser-mcp-server/actions/runs/42189368638) 在 `aaa6055` 上放行 1.0.22。落地过程中，CI 发现 preflight 仍按包内 Node 时期的规则要求 `node.exe` 位于 `USERPROFILE` 内，已修正（见第 5 条）。目标机上的安装和 AppLocker/WDAC 行为仍未验证，证据和未验证项见 `docs/verification.md`。
