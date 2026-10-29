# 第五阶段：整页阅读与 CATIA 帮助宏

版本：0.5.0。当前便携发行物为 `dist/CatiaManualViewer-0.5.0-portable.zip`。本阶段完成第四阶段提出的阅读模式、渐进翻译、缓存、暂停重试和目录宏方案。

## 阅读方式

正文工具栏提供原文、双语和中文三个互斥按钮。默认原文，双语在原段落下追加纯文本译文；中文隐藏已经翻译的普通原文文本，保留图片、代码、链接名称和锚点。未翻译或失败的段落仍显示原文，页面始终可读。按钮提示显示所选服务和当前文章字符量。

视口附近片段优先，其余片段按正文顺序处理。底栏展示完成数和状态；暂停立即停止提交后续片段，已经发出的片段完成后仍会缓存。切回原文也会暂停提交。点击继续、整页重试或原文旁的“重试此段”可继续；连续三段失败时自动暂停，避免失效服务继续消耗请求。没有自动无限重试或隐式整库任务。

划词和整页共享一个低并发调度入口：已有整页片段结束后，等待中的划词请求先运行。切换服务、文档或返回首页后，旧结果只进入原任务的缓存，不会写入新页。HTTP 请求在当前 provider 的超时范围内结束；暂停不会强制中止已经发出的请求。

原始图片、超链接和锚点节点不被译文替换。所有 provider 输出通过 `textContent` 写入隔离 DOM，不解释返回 HTML，旧文档脚本继续禁用。正文容器插入译文前记录视口片段及偏移，完成布局后补偿滚动；切换模式、缓存回填和返回历史页使用相同定位方式。阅读模式与相对位置在当前会话中恢复，译文缓存跨进程保存。

当前中文模式保留原始链接标签和代码，因此可能同时出现少量英文命令名称。首期不对图片文字做 OCR，也不把译文重新拼接成不可信的富文本。离线通用模型对 CAD 句子仍有误译，双语便于核对；常见标题如 Creating Pads 和 About Profiles 使用 CAD 术语表直接生成正确术语。

## 核心与 GUI 分离

| 模块 | 作用 |
| --- | --- |
| `core/reading.py` | 不跨块、表格或锚点边界的阅读片段与稳定 ID；`ReadingPage` 同时包含片段和渲染映射 |
| `core/parser.py` | `render_page()` 返回 `ReadingPage`，`render()` 保留原有 HTML 接口 |
| `core/page_translation.py` | Qt 无关的任务、视口优先调度、失败暂停和 SQLite 缓存 |
| `gui/page_reader.py` | 控件、后台任务、状态、服务快照及 DOM 结果投递 |
| `assets/reader_translation.js` | 仅在 ApplicationWorld 执行的增量更新与相对位置恢复 |

片段按照归一化 HTML 的连续内联内容组织，嵌套段落和表格保持原结构。普通文本可以隐藏，`code/kbd/pre` 等原始内容保留。旧索引的 `Document.blocks` 接口不变，阅读映射使用独立 renderer 版本，不要求重新建立全库 FTS 索引。Agent 可以直接调用 `ManualParser.render_page()` 及核心翻译服务，不需要导入 Qt。

`page_translations` 与现有索引共用 SQLite 文件，但独立建表，不更改现有索引版本。缓存身份包含原文路径、源文件内容哈希、片段 ID / 文本、源渲染版本、目标语言、provider / model 指纹及术语表版本。长段按句子或词边界分为最多 1000 字符的请求；provider 自身更严格的限制仍生效。完成片段逐个提交，部分失败不清空已有缓存。中间片段复用的翻译缓存也使用同一服务版本命名空间。

## CATIA 帮助集成

便携版“更多 → 导出 CATIA 帮助宏…”生成三个 `.catvbs`：

- `ManualViewer.catvbs`：读取工作台与第一个选中特征，传入 `--help-workbench` / `--help-feature`。
- `PadHelp.catvbs`：固定打开 Creating Pads。
- `PocketHelp.catvbs`：固定打开 Creating Pockets。

精确的选中特征映射目前覆盖 Pad、Pocket。工作台映射提供 Part Design、Assembly、Sketcher 等范围入口；其他特征降级到搜索，空上下文返回首页。没有声称 Automation 能取得当前活动命令的 F1 帮助 ID。

查看器实例由程序位置和配置范围区分，默认配置与个人配置使用相同身份。`QLockFile` 协调实例建立，`QLocalServer` / named pipe 限定同用户访问；第二次调用将有大小和类型限制的 JSON 交给原窗口，再退出。请求不包含 shell 命令，文档目标仍经过根目录校验。Windows 二次启动进程通过本地握手把前置权限交给已有窗口。Qt 接口依据 [QLocalServer 官方文档](https://doc.qt.io/qtforpython-6/PySide6/QtNetwork/QLocalServer.html)。

宏使用 `WScript.Shell.Run` 启动明确的便携 EXE；路径被引用，不通过 cmd / PowerShell。B33 实测无法可靠识别 UTF-16 catvbs 中的函数，因此导出 ASCII 文件，并以 UTF-16 码元的 `ChrW()` 表达式保留中文和其他非 ASCII 路径。移动 EXE 后重新导出；宏目录移动后需要重新添加宏库。

CATIA 公共接口证据位于本机：

- `online/interfaces/System/interface_SystemService_13520.htm#ExecuteScript`：执行目录库中的脚本函数。
- `online/interfaces/System/enum_CatScriptLibraryType_22393.htm`：目录宏库类型。
- `online/basug_C2/basugbt1903.htm`：运行宏和把宏拖入工具栏。
- `online/basug_C2/basugcu0102.htm`：创建用户工具栏；自定义信息在退出 CATIA 会话时保存。

没有修改 CATDocView、F1、默认浏览器、注册表或 CATIA 安装文件。本次验证在 CATIA 的用户自定义层添加独立帮助工具栏和目录宏库。

## 验证记录

53 项 pytest 与 Ruff 通过。原生 WebEngine 验证包括原有即时翻译 / 链接 / 导航 12 项、分类 / 设置 / 迁移 / 侧键 18 项、按钮 / 菜单 / 底栏 10 项，以及新整页流程 14 项。新流程覆盖划词优先、渐进显示、暂停、局部失败、单段重试、三种模式、图片 / 链接 / 代码 / 锚点保留、模式切换无重复请求、历史位置恢复和冷任务 SQLite 复用。

真实英文 Creating Pads 使用本机模型翻译 53 个片段，全部完成；GUI 截图在 `data/stage5-real-bilingual.png` 和 `data/stage5-real-chinese.png`。常见标题术语规则随后加强，并在编译版中复核。真实远程服务的额度和输出质量取决于配置，本轮未调用远程翻译 API。

真实 CATIA B33 会话中，`ExecuteScript` 探针返回 `CMV:Assembly`，中文字符串的 ChrW 往返一致。导出的三个宏被宏库识别；从 CATIA 宏窗口运行 PadHelp 成功启动实际 Nuitka EXE，并打开 Creating Pads。通用上下文探针得到 `Assembly / Product`，按设计降级为装配范围中的 Product 搜索。源码双进程及编译版的第二次调用均复用已有窗口。

最终 Nuitka 构建及 10 项运行时自检通过，ZIP 约 348.6 MiB，包含模型、阅读脚本和宏安装说明，不含个人配置或索引。实际配置的本机 EXE 已更新为最终构建，与发行目录 EXE 的 SHA-256 一致。

通过 CATIA 自定义界面创建了独立的 Catia Manual Viewer 工具栏，并把 ManualViewer 命令拖入。真实点击该按钮可以冷启动最终 EXE，显示 Assembly / Product 对应的装配范围搜索；查看器已运行时再次点击会前置并复用同一窗口（验证前后窗口 ID 均为 527176）。验证结束后退出本轮启动的空白 CATIA 会话，保存宏库与工具栏配置；本轮未执行模型编辑或保存命令。

本机已绑定的宏目录位于 `dist/CatiaManualViewer-0.5.0-win64/integration/CATIA`，指向同目录下已更新的最终 EXE；新下载便携包在“更多”中导出自己的宏，不携带本机绝对路径。独立、未安装 Python 的 Windows 电脑仍需迁移验收。
