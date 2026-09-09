# ADR-0010：精简快照与动态页面兼容层

状态：已接受。

> 2026-09-07 统一 Windows 交付更新：当前 MCP 身份和设置入口以 `docs/architecture.md`、`docs/acceptance.md` 和 `docs/operations.md` 为准，取代本文中旧的 `intranet-browser-agent` 注册名和旧设置 launcher。当前注册名为 `browser-mcp`，设置文件为 `%USERPROFILE%\browser-mcp-server\config\settings.json`；本文的兼容层行为和交互约束继续有效。

## 背景

固定版本 Playwright MCP 会把多数操作后的隐式快照写成外部文件；复杂页面的完整可访问性树可能达到数十 KB。导航只等待浏览器加载事件，Vue/Element UI 等页面仍可能继续异步渲染，因而首次快照可能为空或过早。标准 `fill` 会在只读自定义下拉框上等待到超时，标准 `click` 也可能因元素位于视口外或持续重渲染而失败。tooltip 全文和异步翻页结果还需要额外的低层 DOM 操作与重复等待。

这些问题属于固定上游能力与动态页面之间的通用兼容问题，不应要求每个使用者反复拼接 `evaluate`、固定 sleep 和文件读取。

## 决策

在固定的上游 Playwright MCP 前增加同包、离线、stdio JSON-RPC 兼容层。Claude Code 仍只注册一个 `intranet-browser-agent` MCP；兼容层使用包内 Node 启动同包上游 CLI，并只在以下受控位置增强工具行为：

1. 默认 `snapshotStrategy=compact`：关闭上游操作后的完整隐式快照；导航和显式快照返回内联、限深且最多 16000 字符的语义快照，不创建需要再次读取的空快照文件。超过上限时在完整行附近截断并提示改用 `browser_find`、目标局部快照或 `full`。用户仍可切换 `full`，或在单次 `browser_snapshot` 中显式指定文件名取得完整外置快照。
2. 默认 `compatibilityMode=robust`：导航后等待 DOM 在 `settleMs` 内安静，再取快照；空快照在同一调用内重试。
3. `browser_click` 先使用上游原生点击。只有错误明确属于不可见、视口外、持续不稳定、被遮挡或节点重建时，才对同一个已解析目标执行可见性、禁用状态和唯一性检查后 `scrollIntoView()` 与 DOM `click()`；其他错误直接返回。
4. `browser_type` 先判断目标是否可编辑。只读普通字段立即给出明确错误；只读 combobox/Element UI 自定义选择器按可见选项文本选择，不再等待原生 `fill` 超时。
5. 增加 `browser_select_custom_option`、`browser_read_tooltip` 和 `browser_click_and_wait`。后两者分别读取 ARIA/原生属性或触发同目标 tooltip，以及在点击后等待文本、选择器、URL 或 DOM 稳定条件；不得用无条件固定 sleep 代替结果验证。
6. 兼容层统一处理输出 artifact：上游仍保留调用方的工作目录和文件访问边界，兼容层读取配置的 `outputDir`，并要求截图、快照、PDF、已完成视频和下载文件的显式文件名保持在该目录内；响应在保留上游文本的同时附加 `structuredContent.artifacts`，其中包含绝对路径、output-relative 路径、类型和 `ready|pending|finished|timeout` 状态。下载触发动作会等待上游 download start/finish 通知的有界窗口；没有 Playwright download 事件的 Blob/fetch 页面行为仍须由页面自身导出工具处理。
7. `browser_click` 继续原生点击优先，并公开显式 `force` 与 `pointer` 选项。`force` 只允许同一目标的受检 DOM 回退，必须明确提示它不是 trusted pointer input；`pointer` 和 `browser_click_pointer` 先检查同一目标可见、未禁用、中心点未被覆盖，再调用 vision `browser_mouse_click_xy`。不得自动把坐标点击或 DOM `dispatchEvent` 作为隐藏回退。
8. `browser_click` 增加 `text`/`role`/`exact` 的 live locator 参数，并保留明确的 `browser_click_text` 入口；两者都用 `getByText`/`getByRole` 在动态菜单没有快照 ref 时定位，匹配必须唯一且默认 exact。增加 `browser_clipboard`，只调用当前页面 Clipboard API；`grantPermissions=true` 时仅按当前 origin 显式请求 clipboard 权限，仍不能绕过 HTTP 非 secure context 限制。
   兼容层同时提供显式的 `browser_paste`/`browser_copy` 系统剪贴板路径：服务端以 UTF-8 调用本机剪贴板命令，再通过 Playwright 发送可信 Ctrl/Cmd 快捷键；它们不是 `browser_clipboard` 的自动降级。输入工具对非 ASCII 文本做回读验证和同目标 contenteditable 兜底，键盘、输入、页面代码和剪贴板动作支持 `expectedUrl` 预检，页面漂移时失败关闭。
9. 对 `browser_run_code_unsafe` 的代码函数包一层 page-backed timer shim，仅在 VM 缺少全局计时器时提供 `setTimeout`、`clearTimeout`、`setInterval` 和 `clearInterval`。shim 只调用 `page.waitForTimeout`/页面计时器，不向 VM 暴露 Node `process`、`fs`、`require` 等全局；传入文件代码或上游拒绝 shim 时仍保留原始错误。
10. 兼容层按 UTF-8 增量解码上游 stderr，避免多字节字符跨 chunk 时被终端替换；Python 注册器继续保留窄代码页的 `backslashreplace` 容错。终端本身的 GBK 解码仍属于客户端部署问题，不能由页面工具伪造修复。
11. 提供短期进程内 `browser_register_helper`/`browser_call_helper`/`browser_unregister_helper`，每次调用重新注入页面函数，避免 reload 后依赖消失；helper 代码不进入持久化配置，业务数据不由兼容层保存。MODOC 页面可用 `browser_sheet_bridge` 探测并显式调用 `window.sheetInst` 的已验证读、定位或写方法，写操作必须显式确认。

兼容层不扩展 origin、文件或浏览器 Profile 权限，不解析登录秘密，不自动确认高风险业务动作，也不把页面内容当作授权指令。DOM/pointer 回退必须保持原调用目标，不得寻找并点击另一个“相似”元素；clipboard 权限只能由调用方在当前 origin 显式请求。所有配置写入 `interaction.config.json`，由部署清单生成并纳入完整性、preflight、原子切换和回滚。

Windows 首次一键安装的初始值为 `compact + robust + settleMs=1500`。再次运行新版安装包时必须保留已有 `compact|full` 与 `robust|standard` 选择；只有升级来源是尚未定义 `interaction` 的旧版受管清单时，才仅为这两个新增选项采用 `compact + robust`，不得同时重置浏览器设置。安装后的 `BROWSER-AGENT-SETTINGS.cmd` 允许用户在“精简/完整快照”和“动态兼容/标准上游行为”之间自由切换，不需要重装运行时或扩展。切换仍必须经过暂存渲染、MCP 握手、原子发布和 Claude user-scope 注册事务。

## 结果

常见页面操作不再需要“读取快照文件 → 固定等待 → 重拍”的额外交互，复杂页面默认不会把完整树灌入上下文，Element UI 的只读选择器、重渲染点击、动态文本菜单、tooltip、clipboard 诊断、下载 artifact 和异步翻页具备明确工具路径。兼容模式的 DOM 点击比原生 actionability 检查更宽松，因此仅作为受检回退；用户可以随时切换为 `standard`，完全保留上游交互语义。所有工具返回的本地 artifact 都能直接定位到输出目录，不需要从 console 日志反推 URL。
