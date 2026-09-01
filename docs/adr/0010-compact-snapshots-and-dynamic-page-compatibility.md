# ADR-0010：精简快照与动态页面兼容层

状态：已接受。

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

兼容层不扩展 origin、文件或浏览器 Profile 权限，不解析登录秘密，不自动确认高风险业务动作，也不把页面内容当作授权指令。DOM 回退必须保持原调用目标，不得寻找并点击另一个“相似”元素。所有配置写入 `interaction.config.json`，由部署清单生成并纳入完整性、preflight、原子切换和回滚。

Windows 一键安装初始值为 `compact + robust + settleMs=1500`。安装后的 `BROWSER-AGENT-SETTINGS.cmd` 允许用户在“精简/完整快照”和“动态兼容/标准上游行为”之间自由切换，不需要重装运行时或扩展。切换仍必须经过暂存渲染、MCP 握手、原子发布和 Claude user-scope 注册事务。

## 结果

常见页面操作不再需要“读取快照文件 → 固定等待 → 重拍”的额外交互，复杂页面默认不会把完整树灌入上下文，Element UI 的只读选择器、重渲染点击、tooltip 和异步翻页具备明确工具路径。兼容模式的 DOM 点击比原生 actionability 检查更宽松，因此仅作为受检回退；用户可以随时切换为 `standard`，完全保留上游交互语义。
