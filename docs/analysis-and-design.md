# CATIA V5 帮助文档查看器：分析与基本设计

日期：2026-10-29。阶段：环境与基础原型，后续迭代的设计基线。

本文保留第一阶段设计基线；当前 v0.2 实现与边界见 [第二阶段说明](stage2.md)，其中已落地即时翻译、中文查询和完整浏览历史。

## 1. 目标与边界

把本机 CATIA V5 帮助文档变成可以快速搜索、阅读、理解并打开配套模型的本地知识库。核心解析与检索能力独立于桌面界面，后续 agent、CLI、MCP 或本地 HTTP 服务复用同一份 Python API。

原始文档只读，索引与翻译缓存写入项目 `data/`。不复制整套帮助文件，不上传文档，不修改 CATIA 安装目录。模型依赖保持原来的相对目录，打开示例不把 CATProduct 单独拷走。

本轮交付是可运行的基础原型。完整中文翻译、中文搜索、生产级安装包和 CATIA 交互联调是后续工作，不能把术语命中视为整句翻译。

## 2. 本机调查结果

实际检查的路径：

- 文档根目录：`C:\UserApp\3D\DS\Dassault Systemes\B33doc\English`
- 入口：`CATIA_P3_homepage.htm`
- 启动器：`C:\UserApp\3D\DS\Dassault Systemes\B33\win_b64\code\bin\CATSTART.exe`
- 环境名称：`CATIA_P3.V5-6R2023.B33`
- 环境目录：`C:\UserApp\3D\DS\Dassault Systemes\CATEnv`

文件系统普查：

| 项目 | 观察结果 |
| --- | ---: |
| 文件总数 | 80,744 |
| `.htm` | 15,312 |
| HTML 合计 | 15,334（含 22 个 `.html`） |
| HTML 字节数 | 155,752,165，约 148.5 MiB |
| `online` 下含 HTML 的目录 | 364，包含公共资源和接口参考 |
| GIF / PNG | 50,062 / 4,524 |
| CATPart / CATProduct | 3,448 / 1,195 |
| CGR / MODEL / CATDrawing | 811 / 272 / 126 |
| CATScript | 218，作为脚本附件，不直接执行 |

入口页在 `onLoad` 中跳转到 `online/CATIA_P3_default.htm`。首页通过旧 JavaScript 控制导航，正文一般不依赖这些脚本。典型目录：

```text
English/
  CATIA_P3_homepage.htm
  online/
    CATIA_P3_default.htm
    icons_C2/                   # 公共图片、样式与旧脚本
    prtug_C2/                   # Part Design 手册
      prtugbt0501.htm           # Creating Pads
      prtugsm.htm               # 静态 Site Map
      prtugCATIAtoc.htm         # 顶层分类
      prtugsmfrs.htm            # frameset 外壳
      _navigation/prtugnav.htm  # Previous/Next 等导航
      index/ftindex.json        # 原始全文索引，存在 FileList、TitleList
      images/
      samples/Pad1.CATPart
```

`prtugsm.htm` 的 `p.sm1`、`p.sm2` 与 `margin-left:25/50/...` 可以恢复目录层级。`_navigation` 页面包含 `GoToBody('...')` 静态调用，可提取目标，不能执行任意脚本。

正文存在 UTF-8 和声明 `iso-8859-1` 的页面，使用 FrontPage 生成的表格、嵌套段落、命名锚点与相对图片。比如 `Creating Pads` 页直接链接 `samples/Pad1.CATPart`，真实文件存在。

这份文档同时包含自动化接口参考，单纯抓取首页链接会漏掉页面，因此首次索引扫描整个根目录的 HTML 文件。原始 JSON 索引可作为后续元数据补充，不依赖其专有搜索结构。

## 3. 技术决策

| 决策 | 采用方案 | 原因与限制 |
| --- | --- | --- |
| Python | 当前环境 3.12.14 x64，项目声明 3.12–3.14 | WindowsApps 的 python/py 别名执行失败；实际用户安装为 3.15.2，本轮不用它承载 Qt |
| HTML 解析 | BeautifulSoup + lxml | 容忍旧式与不完整 HTML，生成统一模型；不运行脚本 |
| 数据存储 | 标准库 sqlite3 + FTS5 | 单文件、无需服务，足以承载本机约 1.5 万页 |
| 英文搜索 | Porter + Unicode61，前缀索引，BM25 | 标题权重更高、精确标题优先；普通文本输入转义，拒绝 FTS 语法注入 |
| 桌面 GUI | PySide6 Widgets + Qt WebEngine | 旧手册大量图片和表格，浏览器引擎比纯富文本组件更适合保留内容布局 |
| CATIA 集成 | pywin32 COM，回退 CATSTART | 优先复用当前 CATIA 会话；启动参数独立配置 |
| 翻译 | Protocol + provider adapter + SQLite 缓存 | 默认术语辅助；LibreTranslate 可指向自建本机服务或用户指定服务 |
| agent 入口 | Python API + JSON CLI | 本轮不引入网络服务，未来只需薄适配层 |

FTS5 的 Unicode61 不等于中文分词器。原文索引不会因为装了 GUI 就具备中文搜索；后续需要术语查询扩展、中文分词/二元切词及译文索引。Trigram 对少于三个字符的查询存在限制，不能直接当作所有中文短词查询的解决方案。

## 4. 分层与依赖方向

```mermaid
flowchart TD
    GUI[PySide6 桌面阅读器] --> Core[核心 Python 服务]
    CLI[JSON CLI] --> Core
    Future[未来 MCP / HTTP adapter] -.-> Core
    Core --> Parser[HTML 解析与目录恢复]
    Core --> Index[SQLite FTS5 检索]
    Core --> Translation[翻译接口与缓存]
    Parser --> Files[本机只读帮助文档]
    Index --> DB[(manuals.sqlite3)]
    Translation --> DB
    Translation -.用户配置.-> Provider[LibreTranslate 服务]
    GUI --> Launcher[CATIA 启动 adapter]
    CLI --> Launcher
    Launcher --> COM[运行中的 CATIA COM]
    Launcher --> CATSTART[CATSTART -object]
```

模块职责：

- `core/models.py`：Document、Block、Link、SearchHit、TocEntry，数据结构不包含 Qt 类型。
- `core/paths.py`：相对 URL、URL 编码、锚点与文件类型识别；解析后必须落在文档根目录内。
- `core/parser.py`：编码处理、正文块、链接、标题、目录及静态阅读 HTML。
- `core/index.py`：数据模型落库、FTS 同步、增量索引、查询和结构化读取。
- `core/translation.py`：翻译协议、术语表、LibreTranslate 和按内容缓存。
- `integrations/catia.py`：文件校验、COM 复用、启动参数构造。
- `cli.py`：JSON 输入输出的命令适配层。
- `gui/app.py`：Qt 阅读、目录、搜索、选中文字翻译与示例操作。

CLI 的解析、检索、翻译和核心模块导入均不依赖 GUI。索引和翻译在后台线程执行，每个任务独立建立 SQLite 连接；SQLite 使用 WAL 与超时，避免跨线程共享 connection。阅读页解析目前在 UI 线程完成，若将来出现特别大的单页，可再异步化并加入 LRU 页面缓存。

## 5. 核心文档模型

```text
Document
  path             文档根目录下的规范化相对路径，稳定身份
  title / module   页面标题与所属模块
  kind             article / navigation / frameset
  encoding         读取时使用的编码
  content_hash     原始文件 SHA-256
  text             按阅读顺序提取的纯文本
  headings[]       章节标题
  blocks[]         {id, kind, text}
  links[]          {label, href, kind, target, fragment, exists}
```

Block ID 由文档路径、块序号与块内容生成，同一版本解析结果可重复。插入、删除块会影响其后块的 ID；当前不承诺跨版本恒定。agent 引用建议携带 `path + content_hash + block_id`。未来再把锚点、原始 DOM 路径、章节和操作步骤关联进块模型。

`Link.kind` 区分文档、模型示例、普通附件、外部链接和禁止链接。CATScript 不列入模型示例白名单，不能通过一键示例入口执行。文档内容和翻译均视为数据，agent 接入时不能把正文中的指令当系统指令。

## 6. 数据库与增量策略

| 表 | 内容 |
| --- | --- |
| `metadata` | 文档根路径绑定，避免不同安装混用同一索引 |
| `documents` | 页面路径、标题、模块、类别、编码、哈希、mtime、size、正文与结构化 JSON |
| `documents_fts` | 外部内容 FTS5 表，通过触发器与 documents 的插入/修改/删除同步 |
| `translations` | provider + target + source text 的 SHA-256 键、原文、译文与时间 |

`PRAGMA user_version=1` 记录 schema 版本，遇到未知版本直接报错。未来版本写显式迁移，不默默覆盖数据。

增量扫描先比较 `mtime_ns + size`，未变化就跳过；重新解析时计算 SHA-256。该优化不能识别“内容变化但时间与大小被原样保留”的情况，`index --force` 用于完整重新解析。每 100 页提交一次，中断后已提交的进度保留，下次继续。

完整扫描成功后才删除消失页面。限定 `--limit` 或发生解析错误时不清除旧记录；按模块扫描只清理该模块。解析失败的旧记录保留并在报告中列出，下一轮可重试。

当前 `read` 从原文重新解析，确保 agent 能读取未建索引的页面；`ManualIndex.get()` 可返回上次索引快照。索引报告、查询命中和读取结果都支持 JSON。无关导航页不参与默认搜索。

## 7. 阅读与目录

本轮保留正文的图片、表格、列表和锚点，并统一基础字体与样式；移除旧脚本、事件、frames、嵌入对象、外部样式和原来的内联样式。阅读器不是对旧首页做浏览器封装。

目录按模块懒加载，优先使用静态 Site Map 的顺序和层级，没有 Site Map 时回退到模块 HTML 列表。跨文档链接交回应用解析，模型链接交给 CATIA adapter。框架页尝试选择其正文 frame。通用 JavaScript 动态目录、复杂脚本链接和所有框架组合不在本轮完整支持范围内。

Qt WebEngine 禁用 JavaScript 和远程内容访问，并将本地资源约束在文档根目录与应用的 `data/rendered/` 缓存。规范化 HTML 写入该缓存后以本地 URL 加载，避免 `setHtml` 的 data URL 大小限制；实际接口总索引页超过 2 MB。解析后的原始链接仍保存在结构化模型，GUI 只呈现受控的本地内容。页面源文件与数据库不会被自动分享。

## 8. CATIA 示例打开流程

1. 解析链接为根目录内真实模型路径，保留文件名与目录，不搬移依赖。
2. 使用模型类型白名单并确认文件存在。
3. 工作线程初始化 COM，调用 `GetActiveObject('CATIA.Application')`。
4. 已有会话：`application.Documents.Open(path)`；失败明确报错，避免默默再起一个会话。
5. 没有会话：构造 argv，用 `subprocess.Popen(..., shell=False)` 启动：

```text
CATSTART.exe
  -run CNEXT.exe
  -env CATIA_P3.V5-6R2023.B33
  -direnv <CATEnv 路径>
  -nowindow
  -object <示例完整路径>
```

`-object` 的依据是本机官方帮助 `online/basug_C2/basugbt0101.htm` 的 “Method 5: Using the catstart Command”。Windows 带空格路径交给 subprocess 参数列表处理，不手写 shell 拼接。

本轮验证 argv 和模拟 COM 交互。启动命令提交成功不等于模型已加载成功；CATSTART 回退还需要在实际 CATIA 会话中验证许可证、权限、COM 注册以及模型依赖。当前不会从 CLI 默认启动 CATIA，需 `open --execute`；GUI 点击示例属于明确打开操作。

后续可增加打开到用户工作目录的“可编辑副本”模式，但 CATProduct 引用图必须整体处理。本轮直接打开安装目录内的源示例，用户修改后应另存为个人工作文件。

## 9. 中文与翻译路线

本轮：选中文字 → TranslationService → provider → 本地缓存 → 独立侧栏显示。默认 `GlossaryProvider` 仅返回 CAD 术语释义，并明确标记“非整句翻译”。`LibreTranslateProvider` 遵循 `/translate` 接口，读取环境变量中的 API key；只有显式配置该 provider 并触发翻译才向配置端点发送选中文字。

后续：

1. 选择翻译引擎：自建本机模型、LibreTranslate 或用户的模型 API。引擎能力、延迟与专业术语质量需要真实段落对比。
2. 建立可配置 CATIA 术语表，保留 `Pad Definition`、按钮、参数、文件名和 API 标识符。
3. 按段落/操作步骤翻译，保留图像和表格位置，提供中英对照与失败重试；按模型版本、术语版本与原文哈希隔离缓存。
4. 对译文建立单独的中文检索字段，并提供原文回溯。中文搜索可以先做术语扩展，再评估分词和混合检索。

目前适配器契约已用模拟 HTTP 测试；没有部署本地翻译模型，也没有实测某个真实服务的翻译质量。

## 10. 迭代与验收

| 阶段 | 验收目标 |
| --- | --- |
| 当前基础阶段 | 独立环境、设计、核心解析/索引/CLI、桌面原型、真实全库扫描与测试记录 |
| 下一阶段：可用性 | CATIA 两条启动路径联调，锚点与复杂 frames 回归，中文模块名称和更好的查询排序 |
| 中文阅读 | 确定 provider、术语表、分块双语阅读、翻译队列、中文搜索 |
| agent 接入 | 章节/步骤/资源结构完善，稳定引用，MCP tools 与只读本地 API；模型打开作为显式有副作用工具 |
| 交付 | 用户配置界面、索引迁移、日志、Windows 安装包、Qt/Chromium 与第三方许可证材料 |

建议的真实验收语料包括：Part Design、Assembly Design、Generative Shape Design、Sketcher、Automation interfaces；覆盖英文短词、文件名、跨模块跳转、2D/3D 示例、缺失附件、非 ASCII 字符和非常大的表格页。

## 11. 技术参考

- [SQLite FTS5 官方说明](https://www.sqlite.org/fts5.html)：tokenizer、前缀查询、BM25、trigram 限制与外部内容表。
- [Qt for Python 入门](https://doc.qt.io/qtforpython-6/gettingstarted.html)：PySide6 安装和虚拟环境。
- [QWebEngineView](https://doc.qt.io/qtforpython-6/PySide6/QtWebEngineWidgets/QWebEngineView.html)：HTML 阅读与页面加载。
- [QWebEnginePage](https://doc.qt.io/qtforpython-6/PySide6/QtWebEngineCore/QWebEnginePage.html)：链接导航拦截。
- [LibreTranslate `/translate`](https://docs.libretranslate.com/api/operations/translate/)：请求字段与 translatedText 返回值。
- CATIA 本机安装文档：`online/basug_C2/basugbt0101.htm`、`online/prtug_C2/prtugsm.htm` 与 `online/prtug_C2/prtugbt0501.htm`。

## 12. 知识导航规划（2026-10-30，设计稿）

本节对应当前结构化搜索、双语词库与来源契约完成后的下一阶段。以下均为待实施设计，本轮不增加图服务、图形组件、模型或新索引。

### 产品顺序与入口

| 视图 | 解决的问题 | 首版内容 |
| --- | --- | --- |
| 功能地图 | 我在什么功能范围，还可以做什么？ | 功能分类 → 手册 → 章节 → 操作教程；当前页面、同级功能和返回上级 |
| 页面关系图 | 这个功能有什么参考说明、相关操作和示例？ | 当前文章为中心的局部网络，按关系类型展开，直接回到有依据的原文 |
| 个人思维导图 | 如何组织自己的学习和设计任务？ | 从现有页面创建卡片，自定义分支、备注、顺序与连线，保留原文来源 |

建议先交付功能地图，再交付页面关系图，最后开放个人编辑。三种视图复用一套图数据契约与来源定位，界面根据任务分别使用层级、局部网络和自由布局。

地图作为阅读区域的可切换视图，不新增常驻侧栏。功能首页可进入当前分类地图，文章工具栏可进入当前功能地图或页面关系图；搜索结果可在当前地图中聚焦。切换保留查询范围、正文位置、阅读模式、当前节点和视口，不重新载入整个工作区。

以“相合约束”为首个验收场景：搜索进入 Managing Coincidence Constraint，地图显示“装配设计 → 用户任务 → 管理约束”，并能看到偏移约束等同级教程；点击参考关系到原文参考页。同一共享文章位于装配与焊接时，保留两个目录位置及用户选中的上下文。

### 数据来源与边的含义

先使用已有证据，不再增加一个模型来猜测功能归属：

| 已有数据 | 图中的内容 | 关系含义 |
| --- | --- | --- |
| ManualCatalog、manual_placements | 分类、手册、章节及出现位置 | contains：原始目录的层级 |
| manual_membership | 手册与文章的收录 | includes：显式收录；不等同于物理文件目录 |
| article_commands、随包双语命令词库 | 命令概念及其双语名称 | documents_command：该页面记录了此命令，不自动断言它是教程 |
| documents.links、来源块 | 文章链接、示例、图片 | links_to、has_sample、has_image：原文中实际存在的资源关系 |
| manual_keywords | 关键词入口 | keyword_entry：原手册关键词指向的页面或锚点 |
| 用户编辑 | 学习分支、笔记及自定关系 | user_link：个人组织，不写成原手册事实 |

目录相邻只表示同级，不推断操作先后；页面超链接只表示引用，不推断“必须先学”。后续语义相关推荐以独立的 suggested_related 关系和虚线呈现，默认隐藏，不混入显式关系。只有未来能取得具体步骤证据时，才考虑 prerequisites 等更强的关系类型。

同一文章只建立一个文章节点，目录出现位置单独建模；不因共享手册复制正文节点，也不把共享目录内所有文件一概收入某个工作台。中英文名称作为同一个命令概念的标签，页面身份仍属于用户实际文档库，不因术语通用而合并不同版本的原文。

### 核心契约与存储草案

核心增加独立于 Qt 的 GraphService，提供 map(scope)、neighbors(node, filters, cursor)、locate(SourceLocation)、export(snapshot) 等有限接口。输入包含库身份、功能范围、关系类型、展开深度与数量上限，输出为可序列化的 GraphSnapshot：

| 类型 | 必要字段 |
| --- | --- |
| GraphNode | id、kind、label_en、label_zh、document_ref、placement_context、resource_ref |
| GraphEdge | id、source、target、kind、provenance、evidence[]、生成修订 |
| GraphSnapshot | schema_version、library_id、graph_revision、nodes、edges、truncated、cursor |
| GraphViewState | view、scope、selected_node、expanded_nodes、positions、pan、zoom、reader_source |

evidence 使用现有 SourceLocation；目录节点还记录 TOC 路径与原始链接证据。分类或用户自建分组可以没有文档来源，但文章、引用边和资源边必须能追溯。provenance 区分 catalog、document、user 和未来的 semantic，避免把评分数值解释为事实可信度。

文章节点使用已有 document_id；命令节点使用有命名空间的 CATIA V5 命令 ID；分类和手册节点包含 library_id。当前 placement ID 含目录序号，目录插入项后可能变化，不能直接作为永久收藏身份。目录出现节点须带 graph_revision；恢复视图优先按 document_ref、manual、fragment、标题路径定位，歧义时回到手册层级，不盲用旧序号。

首版优先投影已有 SQLite 表。若局部遍历确实需要缓存，再增加可重建的 graph_nodes、graph_edges、graph_evidence 及双向邻接索引，记录结构修订、术语修订与内容 hash。无需另行运行数据库服务器。用户导图与布局保存为独立的 user_maps、user_nodes、user_edges、map_layouts；重建自动图不得删除用户数据。

图契约使用库内相对路径及文档身份，不导出安装绝对路径、API Key 或机器特定设置。迁移继续复用 B 轮文档库挂载与来源重定位。没有 CATIA 时保留全部导航、搜索、图片和文件定位能力，仅按能力状态调整模型打开动作。

### 交互与性能边界

功能地图采用可折叠的层级布局，先显示当前手册附近的两层；页面关系图默认一跳，按教程、参考、命令、示例分组。默认目标约 30–80 个节点，硬上限初拟 200 个节点，达到上限时显示分组数量或继续展开入口，不一次绘制整库 15,000 余页。数量约束应在遍历时生效，并处理环和重复节点。

单击聚焦并预览节点，双击或 Enter 打开文章；拖动平移、滚轮缩放，提供回到当前页、适应窗口和上一级三个图标操作。键盘方向键按可见节点移动焦点。节点保留位置，展开时只布局新增分支，不让整张图持续漂移；拖动时降低边和标签绘制复杂度，布局放到后台计算，过期请求按 generation 丢弃。

普通文章导航仍复用当前前进后退及 SourceLocation。纯粹平移缩放不产生浏览历史；切换图视图或打开文章时记录工作区状态，返回后恢复图视口与原文位置。高亮沿用当前搜索术语与配色；中文标签缺失时回退英文。关系名称和出处放在选中或悬停信息中，不铺满常驻说明文字。

### 渲染方案与便携约束

候选采用独立的 QWebEngineView 承载本地 Cytoscape.js，复用已经分发的 WebEngine。其官方文档提供 JSON 序列化、布局、事件和视口交互接口，适合先做局部图原型，见 [Cytoscape.js 文档](https://js.cytoscape.org/)。具体版本在原型通过验收后固定，JS、样式和许可证随 Nuitka 包分发，运行不依赖 CDN、Node.js 或网络。

图视图仅执行随包可信代码，与保留禁用原文脚本策略的阅读器分开。桥接只开放有限的图查询和已验证来源跳转；所有节点数据作为 JSON 传递，不拼接成脚本、SQL 或 shell 命令。来源须按当前文档库校验，不能通过节点输入访问任意本机文件。

这些是待验证的选择：先用本机普通模式与软件渲染验证 80/200 节点的拖动、缩放、中文标签和高 DPI，再确定渲染器；现有 GPU 初始化受限环境必须可用。响应目标初拟为缓存局部图打开 300ms 内、展开反馈 100ms 内、常规拖动接近 60fps，均为验收目标，尚无实测结论。

### 建议实施轮次

1. G0：图数据契约和只读核心投影；以装配约束及一个零件设计场景验证多目录、证据、环、范围过滤和迁移。
2. G1：功能地图的只读交互，打通搜索聚焦、目录上下文、正文和历史；作为下一轮推荐范围。
3. G2：页面关系图，加入参考、命令、示例、图片及按关系展开；实现可重建缓存和局部更新。
4. G3：个人思维导图，增加分支、笔记、排序、撤销重做、自动保存，以及 JSON / PNG 导出。用户资料与自动图彻底分离。
5. 后续再评估语义建议、学习路径和带来源的问答；先用真实用户查询衡量改善，再决定是否增加模型。

每轮在开发机手工运行适当核心与原生界面验收，CI 继续只做构建、打包和制品校验。本轮不安装或实现上述组件。
