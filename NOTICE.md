# 浏览器助手分发与第三方声明

本仓库及其 Windows 分发包供组织内部使用。除组织另行书面授权外，本声明不授予本项目代码或商标的额外权利。

Windows ZIP 会携带经过审核的第三方运行时和浏览器扩展。分发包使用的上游项目、版本和许可证入口如下；使用者应同时遵守对应上游项目随版本发布的完整许可证和声明：

| 组件 | 版本 | 上游与许可证入口 |
| --- | --- | --- |
| Node.js（Windows x64） | 由 `BUILD-METADATA.json` 记录 | [nodejs.org](https://nodejs.org/) 的许可证与第三方声明 |
| `@playwright/mcp` / Playwright | `0.0.79`（锁定版本） | [github.com/microsoft/playwright](https://github.com/microsoft/playwright) 的许可证与声明 |
| Playwright MCP Extension | 与 `config/playwright-extension-source.json` 一致 | [github.com/microsoft/playwright](https://github.com/microsoft/playwright) 的许可证与声明 |
| `chrome-devtools-mcp`（可选依赖） | `1.8.0`（锁定版本） | [github.com/ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp) 的许可证与声明 |

发布方必须在生成 ZIP 时保留本文件，并在发布记录中保存实际 Node.js 来源、哈希和运行时清单。若上游分发包包含更完整的 `LICENSE`、`NOTICE` 或第三方清单，应以这些文件为准并一并保留。
