# 第四阶段：便携版、阅读交互与后续设计

本轮实现交付方式与界面调整；整页翻译和 CATIA → 查看器集成仅完成设计，未加入功能开关或修改 CATIA 设置。

## 便携版交付

使用 Nuitka `standalone` 生成 Windows x64 目录，再压缩成 ZIP。使用者解压整个目录后，只运行 `CatiaManualViewer.exe`，不安装 Python、虚拟环境或源代码。QtWebEngineProcess.exe 是内部浏览器子进程。原有 `scripts/start.ps1`、快捷方式生成脚本、仓库根目录快捷方式和 GUI console entry 已移除。源码 CLI、bootstrap、测试和构建脚本仅供开发者使用。

选择目录式便携版，是因为 QtWebEngine 的资源、DLL 和双向模型适合随程序放在固定目录。以后如果评估 onefile，需要另行处理解压启动时间、外部模型和可写配置；目前构建脚本没有混用两种模式。[Nuitka 官方文档](https://nuitka.net/user-documentation/user-manual.html)说明 standalone 能脱离 Python 安装运行；其[文件定位说明](https://nuitka.net/user-documentation/common-issue-solutions.html#standalone-finding-files)强调不能依赖启动时的工作目录。

编译模式只从 EXE 所在目录读取 `config.local.toml` 或 `config.default.toml`。设置保存到 `config.local.toml`，不覆盖发行默认值。文档、索引、模型和 CATIA 路径中的相对路径均以配置所在目录为准。第一次未找到文档时自动打开设置，用户选择包含 `online` 的文档根目录。

发行默认值不含本机 B33 安装路径、密钥、个人配置、索引或阅读缓存。索引从用户文档副本创建；外部文档路径在迁移后重新指定。自带模型的版本默认启用本机模型，不带模型的版本默认只用术语表，在线源在设置中选择。API Key 延续进程内或环境变量方式，不进入发行包。

构建入口是 `scripts/build_portable.py`。默认不会覆盖已有输出目录；包含 Qt 插件、WebEngine 资源、应用图标和 CT2 的 DLL 配置。编译完成后先从另一个工作目录运行实际 EXE 自检，检查 FTS5、COM、翻译依赖、图标、WebEngine 子进程、MSVC 运行库，以及随包模型的双向推理，再生成 ZIP。第三方依赖版本清单及可获取的许可证文本随包保存；正式公开发布前仍需检查 Qt / 模型等完整分发义务。

## 已实现的交互调整

| 控件 | 操作 |
| --- | --- |
| 后退 / 前进 | 独立按钮，一次点击跳转一次；保留 Alt 方向键和侧键 |
| 浏览记录（时钟图标） | 独立弹出菜单，分别列出后退和前进记录；不可用方向禁用 |
| 设置（滑杆图标） | 直接打开全局设置，支持 Ctrl+, |
| 更多（…） | 更新索引、显示阅读侧栏 |
| 分类下拉框 | 给文字预留箭头区域，使用明确的 SVG 箭头 |

按钮不再使用 `MenuButtonPopup` 的分裂点击区域。每个操作只有一个命中区域，菜单不会挤占图标。历史菜单保留原先恢复锚点和滚动位置的逻辑。

底部右侧永久状态显示服务、模型、即译暂停状态及“翻译中 / 已完成 / 本地缓存 / 未完成”；长信息可以通过提示查看。左侧仍用于索引、搜索、文档加载等临时消息。翻译框只显示原文、译文和必要的 CAD 术语。切换服务、页面或选区会失效化旧请求，旧结果不会覆盖新服务状态。

## 整页翻译：建议的用户体验

在正文上方增加一个轻量的三段切换：`原文 | 双语 | 中文`。默认原文，首次切换双语或中文时才提交当前文章的翻译任务；不会因为打开页面或后台建索引就批量翻译整个文档库。不要在当前界面放一个不可用的占位按钮。

双语采用“原文段落下紧接译文”，而不是左右两栏。这能保持 CATIA 图示、步骤、表格和示例链接的阅读顺序，也适应较窄窗口。中文模式替换可翻译文本，保留命令英文名、文件名、参数、代码、链接和图片；可从段落上的轻量操作临时查看原文。图片内的英文首期不做 OCR，避免额外延迟及术语错位。

一次具体流程：

1. 用户打开 Creating Pads，点击“双语”。已经缓存的段落立即出现译文，其余仍显示原文。
2. 底栏显示“本机模型 · 本页 6/24 · 暂停”，首先翻译视口附近段落，再继续剩余正文。
3. 用户滚动或选择单词，阅读不中断。划词翻译优先于后台整页任务，不能被整页任务占满队列。
4. 用户切回原文，立即隐藏译文；正在执行的一次请求可以完成并缓存，其余任务暂停。回到双语后继续，无需重复付费。
5. 某段失败只保留该段原文，并提供段内重试；底栏给出“2 段未完成 / 重试”。不弹模态错误，不把整页恢复成空白。
6. 离开页面取消未提交任务；已发出的请求按 provider 能力尝试取消，返回结果可入缓存，但不得写入新页面。重新打开时恢复该页的阅读模式和段落位置。

使用远程源时，在阅读模式菜单中显示服务名称、本次发送范围和当前文章的大致字符量；用户选择模式即启动当前文章的翻译，底栏可随时暂停，不额外弹确认框。费用估算仅在已有定价配置时显示，不猜测账单。整库批量翻译将来应作为单独任务提供，不能由当前页面模式隐式触发。

## 整页翻译：核心实现边界

目前 `Document.blocks` 已有文本块 ID，但 `render()` 独立生成 HTML，渲染 DOM **没有相应的块 ID**。因此不能直接把当前 block 列表一对一替换回 DOM。下一轮第一步应提取共同的文档中间表示，让解析和渲染共用节点映射。

建议加入与 Qt 无关的 `PageTranslationService`、`TranslationJob` 和 `RichTextSegment`：

- 使用 `document path + source hash + block ID + source/target language + provider/model fingerprint + glossary revision + renderer revision` 标识可复用结果。模型指纹不能包含 API Key；当前划词缓存仍可独立使用。
- 共享规范化树为段落、标题、列表项和表格单元标记 `data-block-id`，并保留原始 anchor / href / 图片及可翻译的内联文本。代码、模型文件名、变量和 URL 为只读 token。
- 后端拿结构化片段及可验证的占位符，单段翻译并校验链接与 token 完整性。不能把整份原始 HTML 发给模型，也不能信任返回 HTML。首期以原文 + 纯文本译文实现双语，保证链接仍存在于原文；中文富文本替换在 token 校验通过后才上线。
- 本地模型按句子/段落处理；在线源按照各自字符/token 上限分批。失败、限流和超时属于具体批次；限制并发，遵循 Retry-After，有上限重试，支持暂停和明确的部分成功状态。
- 单一调度器为划词翻译保留优先级，整页任务限制在少量并发。请求快照包含页面、服务和 generation；更换服务后缓存分别命中，旧返回不覆盖当前页。
- GUI 仅订阅任务进度及段落结果。用现有 ApplicationWorld 通道更新对应节点，不启用旧文档脚本。插入译文前后以当前可见块及相对偏移保持位置，防止段落增高导致阅读跳动；锚点和历史恢复使用相同定位机制。
- 译文缓存可形成后续中文全文索引的数据来源，但只索引成功结果，并保留原文路径、块 ID 和来源。当前范围筛选和原文检索不等待整库翻译。

下一轮验收重点：含链接的长段落、嵌套列表、表格、重复内容、超长 API 页面、滚动中插入译文、切换服务时过期返回、返回上一页、部分限流失败、完全离线复用缓存，以及复制文本时不会同时复制隐藏的另一语言。先完成双语，再实现中文替换；两者共享任务与缓存。

## CATIA → 查看器：本机证据

以下资料均来自本机 V5-6R2023 B33 文档，可直接在查看器中阅读相对路径。

| 官方文档 | 已确认的事实 |
| --- | --- |
| `online/basug_C2/basugbt0204.htm`，Getting Contextual Help | F1 / Help > CATIA V5 Help 按当前命令打开相应 HTML；仍在运行的某些非当前命令可能有优先级 |
| `online/basug_C2/basugbt0203.htm`，Accessing the Online Help Library | CATDocView 和 General > Help 的 Location 指向文档位置；要求实际有效位置；Windows 也支持 UNC |
| `online/basil_C2/basilInstallDoc.htm`，Installing the Online Documentation | 文档可以部署在 HTTP 服务器；这一点不等于可把 EXE 直接填进文档路径 |
| `online/interfaces/InfInterfaces/interface_DocumentationSettingAtt_38404.htm` | Automation 暴露 TechnicalDocumentationPath、DocLanguage、CompanionPath、Priority 及相关设置方法；未发现任意帮助处理器或回调注册属性 |
| `online/interfaces/InfInterfaces/enum_CATDocContextualPriority_38535.htm` | Priority 只在 Technical Documentation 与 User Companion 之间选择，不能据此推断可以设置外部帮助 EXE |
| `online/basug_C2/basugbt1903.htm`，Running Macros | Tools > Customize > Commands > Macros 可以将宏拖到工具栏，提供明确、可点击的入口 |

`CATIA_P3_OpenPageFind.htm` 是旧 Java 搜索 applet 包装页，名称本身不能证明它是 F1 的公开 hook。当前已实现的 `--open` 是查看器接收本地文档目标的能力，不代表已经接管 CATIA 的帮助分发。本轮没有更改 CATDocView、注册表、默认浏览器、CATIA 工具栏或安装目录。

## 可行路线与建议顺序（均待实现）

| 路线 | 可达效果 | 主要限制 | 建议 |
| --- | --- | --- | --- |
| 自定义帮助宏 / 工具栏按钮 | 点击按钮启动便携 EXE，传入已知主题或工作台入口 | 一般宏没有现成、已验证的“当前命令帮助 ID”回调；固定主题容易，通用上下文需另查 API | 先做低成本验证 |
| 本机文档代理 / URL 桥接 | CATIA 继续解析命令上下文，将最终 topic URL 转交查看器 | B33 在 Windows 上是否允许指向本机 HTTP 根目录、实际 F1 URL 形态、是否仍强制先开浏览器，都需实测；HTTP 可部署不等于已确认可接管 | 优先研究真正 F1 保真方案 |
| CAA RADE 原生命令 / Add-in | 按公开的命令框架实现帮助按钮，控制主题和上下文 | 需要相应 SDK、许可、B33 ABI 和部署环境；不能假设 Automation 等同于 CAA | 宏无法满足上下文时再评估 |
| 改默认浏览器 / HTML 文件关联 / 全局按键拦截 | 可能截获部分打开行为 | 影响其他应用，容易依赖窗口焦点、语言和版本；不能保证拿到准确命令上下文 | 不作为产品路线 |

建议先验证“工具栏帮助按钮 → 已知主题”，再研究 F1 原生的主题定位结果能否被桥接。查看器端需要新增单实例激活与 IPC：第二次调用把规范化主题交给已有窗口、恢复并置前，而非每次开新窗口。可考虑同用户范围的 QLocalServer / named pipe，校验请求大小、相对文档路径及允许的主题类型；传输结构化消息，不执行命令字符串。

未来协议建议为 `library/release + relative path + fragment + optional command ID`，便携程序路径由集成配置管理，不能固化 `C:\Code`。工作台 ID、特征类型可作为降级搜索线索，但不能等同于当前活动命令。无匹配时打开相应分类或带范围的搜索，说明未找到精确主题。

下一轮研究的具体验证：在用户授权修改测试环境后，选 Pad / Edge Fillet / 非模态 Capture 三个命令，记录 F1 打开的实际 URL；检查设置允许的目标类型、URL 查询参数及本机资料中的命令帮助映射；验证是否能稳定传入 `--open` 对应的相对页与 anchor。只有证据支持后才决定是否采用 HTTP 桥接或 CAA。本轮不提供一个未经验证、可能改坏 CATIA 帮助路径的脚本。

## 验证

自动测试覆盖便携模式忽略工作目录配置、默认与个人配置分离、带中文安装路径和整个目录迁移。真实 GUI 验证覆盖后退 / 前进点击、历史菜单方向、维护菜单、直接设置入口、翻译底栏与移除翻译尾注。另回归选词、整句、过期翻译、跨页链接、示例路由、分类搜索及侧键按下 / 弹起。

本机结果：43 项 pytest 通过，Ruff 通过；阶段 2 / 3 / 4 原生 GUI 共 40 项检查通过。Nuitka 4.3.1 + Python 3.12.14 + GCC 15.2.0 实际构建成功，含英中双向模型的最终包为 `dist/CatiaManualViewer-0.4.0-win64.zip`。

最终 EXE 从其他工作目录执行自检，10 项检查全部通过：HTTP 客户端、pythoncom、win32com、CT2、SentencePiece、FTS5、图标、WebEngine 子进程、MSVC 运行库和离线双向推理。随后通过 computer-use 检查该 EXE 的真实窗口：正文与图片正常，分类下拉箭头清楚，Ctrl+, 打开设置；双击正文的 pad 后自动显示“凸台（拉伸）”，服务和“已完成”显示在底栏，译文无服务尾注。窗口检查使用 `.cache/portable-ui.toml` 独立配置，不污染发行目录默认值。

仍需在没有 Python / CATIA 的独立 Windows 电脑验收；本机成功不等于所有 Windows 环境均通过。CATIA hook、整页翻译与真实 CATIA 模型打开均不在本轮已实现或实测范围内。
