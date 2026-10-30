# P0–P3：命令词库、功能归属与文章检索

## 本轮交付

本轮把命令、文章和目录位置作为搜索的主要依据。已识别的中英文命令或明确页名会先定位文章，在混合／语义模式下也不为这些查询加载模型。保留既有模型和向量数据，本轮没有安装新模型或运行模型推理质量测试。

功能范围包含手册自身的模块及原始目录、关键词索引明确收录的共享页面。例如装配设计的约束教程位于 cfyugasm_C2，仍属于装配设计范围；一个页面也可以同时出现在焊接设计的目录中。范围不会因收录一个共享页面而自动扩展到整个共享目录。

正文打开成功后，功能导航会展开并选中对应节点，面包屑提供上级入口，返回搜索结果保留查询、结果和选中项。共享页面可选择“其他位置”，前进后退恢复原手册上下文。阅读位置与搜索范围独立，打开全局结果不会自动缩小下一次搜索的范围。未变化的目录节点复用，保留展开状态。

教程页若明确引用了已索引的参考文章或工具栏说明，提供“参考说明”入口；来源引用继续使用 B 轮 SourceLocation v1。

## 开发机生成、运行时分发

词库文件为 `src/catia_manual/assets/command_terms.json`，随 Python 包及 Nuitka 制品分发，约 356 KB。运行时只读取该资源，不访问用户 CATIA 安装目录，不要求安装 CATIA，也不要求版本与开发机一致。

开发阶段使用 B33 的英文、Simplified_Chinese 消息资源和本机文档，生成 782 个具有文档命令锚点证据的双语命令条目。每条记录包含命令标识、英文和中文名称、简短说明及资源文件／键名来源。另有明确标记的编辑别名，例如“管理约束”“约束管理”对应 Managing Constraints。

`provenance.source_release` 仅记录提取来源，`version_policy=informational-only`，不参与用户版本校验。revision 是词库内容指纹，用来刷新本地检索结构，不是 CATIA 版本门槛。

用户手册通过实际存在的命令锚点和原始关键词索引与词库关联；不保存开发机文章的绝对路径，不把 B33 的文章路径硬编码为搜索目标。其他版本缺少锚点时仍可通过原始关键词、标题和正文匹配。资源对应不是完整中文手册翻译，也不能保证覆盖所有版本的命令。

提取工具识别传统编码、字符串转义、跨行内容和注释，按同名文件与完整资源键配对。注释中的历史编码字节不会污染字符串解码；两个未通过严格解码的资源文件记录在 extraction_errors，未导入疑似乱码。本次生成的 782 个条目均有文档锚点证据。

开发命令：

```powershell
.venv\Scripts\python.exe scripts/build_terminology.py `
  --installation 'C:\UserApp\3D\DS\Dassault Systemes\B33' `
  --documentation 'C:\UserApp\3D\DS\Dassault Systemes\B33doc\English'
```

不将该提取步骤加入 CI 或用户启动流程。发行制品校验要求词库 JSON 存在，运行库诊断也检查词库可读。

## 索引结构与排序

数据库保留 schema 2，新增独立可重建结构 `structure_version=articles-1`：

| 数据 | 作用 |
| --- | --- |
| manual_placements | 手册、父节点、顺序、标题、目标文章／锚点及完整目录路径 |
| manual_membership | 手册与共享文章的多对多归属 |
| manual_keywords | 原手册关键词到页面／锚点的明确关联 |
| article_metadata | 文章角色、作者任务摘要、命令词、索引词、目录上下文和中文标题标签 |
| article_commands | 实际页面锚点与随包命令标识的对应 |
| articles_fts | 文章级标题、命令、关键词、摘要、目录上下文和正文检索 |

FTS 使用现有 documents 的正文视图作为外部内容，不重复保存整篇正文。原章节索引继续用于段落定位、跨章节解释和旧库回退。文档更新、删除通过事务和触发器同步文章索引；目录或词库变化重新整理上下文，其他情况复用未变化的文章元数据。

排序先应用功能范围，再区分证据层次：

1. 明确的完整页名，包括已整理的中文对应。
2. 命令标识或原始关键词直接对应的操作教程。
3. 标题完整覆盖查询概念。
4. 命令名、原手册关键词完整覆盖。
5. 作者任务摘要和普通正文。

同层使用字段权重排序，文章级候选排序发生在结果限量之前。目录、索引页自身不作为操作答案，但其归属和关键词进入文章检索。文章选定后，命令／页名查询到达文章入口，正文查询定位到相关块；跨章节匹配保留说明。

因此 Coincidence Constraint 优先进入 Managing Coincidence Constraint；明确查询 Coincidence Constraints 时仍优先参考文章。结果包含 page_role、title_zh、placement、related、命中理由及层级信号，保持原有英文 title 和来源字段兼容。

## 已有索引升级

已有索引第一次启动会后台补充目录和文章搜索结构，开始前保存一次 SQLite 一致性备份到索引旁的 backups。无需重建向量或重新翻译；目录整理期间可继续阅读，完成后刷新当前搜索。失败时保留已完成元数据，后续可重试或通过“更新文档索引”继续。

首次完整整理需要读取手册的结构信息；本机 15,334 个文档的独立副本首次整理约一分钟，已完成后再次检查约一秒。耗时与文档规模、磁盘有关。启动时已就绪的结构不会重复整理。

开发／agent 接口：

```powershell
.venv\Scripts\python.exe -m catia_manual structure
.venv\Scripts\python.exe -m catia_manual search '相合约束' --manual asmug_C2 --explain
.venv\Scripts\python.exe -m catia_manual locations online/cfyugasm_C2/cfyugasmut0301.htm --manual asmug_C2
```

`--manual`、`--category` 使用功能归属；既有 `--module` 仍表示磁盘模块限制。核心及 JSON CLI 不依赖 Qt，也不需要 CATIA 运行。

## 真实库验收

在现有索引的独立一致性快照中测试，全部 15,334 个文档参与候选，并整理出 14,372 个目录位置。没有缩减为少量文章，没有修改原索引或本机手册。

| 查询 | 全局／机械设计／装配设计的首项 |
| --- | --- |
| 相合约束 | Managing Coincidence Constraint |
| 偏移约束 | Managing Offset Constraint |
| 管理约束 | Managing Constraints |
| Managing Constraints | Managing Constraints |
| Coincidence Constraint | Managing Coincidence Constraint |

上述 15 个检查全部通过。焊接范围另验证相合、偏移教程正确出现；该手册未收录装配的 Managing Constraints 概览，因此两项概览查询检查范围边界，没有为了得到预定结果引入范围外文章。合计 20 个查询／边界检查通过；明确参考页名另行检查通过。进程没有加载 Qt、ONNX Runtime 或 tokenizer。

核心测试 120 项通过；新增 GUI 验收 16 项通过，使用改变了磁盘目录名的构造手册，验证没有 CATIA 安装和模型时的命令搜索、共享页面、目录联动、参考阅读、搜索保留和历史恢复。本机报告为忽略提交的 `data/structured-search-real.json`、`data/structured-gui.json`，截图为 `data/structured-navigation.png`。

另通过 A 轮阅读与资源交互 18 项、B 轮文档库与来源定位 15 项、整页翻译 14 项 GUI 回归。术语窗口显示并可筛选随包词库，用户覆盖仍独立保存。SearchHit.heading_path 保留正文标题层级语义，目录路径放在 placement.trail，来源协议不变。

开发验收入口：

```powershell
.venv\Scripts\python.exe -m pytest --basetemp=.cache/pytest-structure -q
.venv\Scripts\python.exe scripts/verify_structure_gui.py
.venv\Scripts\python.exe scripts/verify_structured_search.py
# 仅复用该验证脚本自己的隔离副本
.venv\Scripts\python.exe scripts/verify_structured_search.py --reuse
```

这些验收不加入 CI。CI 保持仅构建、打包与制品校验；本轮未重新执行整包 Nuitka 编译或发布。
