# 开发与构建

以下命令仅用于仓库开发；使用者从便携版 EXE 启动。

## 开发环境

Python 3.12–3.14。当前 MinGW64 打包固定用 3.12；3.13 / 3.14 请选择 MSVC，具体支持以 Nuitka 对应版本为准。

```powershell
.\scripts\bootstrap.ps1 -PythonExe 'C:\path\to\python.exe'
# 可选：-SkipModels 或 -CoreOnly
.\.venv\Scripts\python.exe -m pip install -e '.[gui,catia,translation,offline,search,dev,packaging]'
.\.venv\Scripts\python.exe -m catia_manual doctor
.\.venv\Scripts\python.exe -m catia_manual gui
```

开发配置在 `config.local.toml`；初始模板 `config.example.toml` 包含当前调查机器的路径，**不能复制进发行包**。`packaging/config.default.toml` 才是便携版模板。

## Nuitka 构建

GitHub Actions 的构建和发布入口、权限与首次标签步骤见 [CI 与 Release](ci-and-release.md)。

```powershell
# 不含模型的版本，仍可在设置中选择在线 API 或另附模型
.\.venv\Scripts\python.exe scripts/build_portable.py

# 包含模型的完整便携版；输出目录必须尚不存在
.\.venv\Scripts\python.exe scripts/build_portable.py --models data/models --output-dir dist/CatiaManualViewer-0.5.0-portable

# 机器已有 MSVC 2022 时
.\.venv\Scripts\python.exe scripts/build_portable.py --compiler msvc
```

默认编译器由 Nuitka 下载到 `.cache/nuitka`，报告在 `.cache/nuitka/compilation-report.xml`。构建脚本不删除、覆盖已有发行目录，以免误写正在使用的便携数据；重复构建选新输出目录。成功后得到同名目录和 ZIP，分发 ZIP，不能只发 EXE。

构建会执行实际 EXE 的开发自检，从不同工作目录检查配置定位及关键运行库。也可手动运行隐藏诊断参数 `CatiaManualViewer.exe --diagnostics <绝对 JSON 输出路径>`；不访问远程 API 或启动 CATIA。编译成功后还应使用真实窗口验证图片、选词、搜索和链接，并在没有 Python / CATIA 的独立 Windows 环境验收。

## 核心与 agent 开发接口

```powershell
.\.venv\Scripts\python.exe -m catia_manual index
.\.venv\Scripts\python.exe -m catia_manual search '圆角' --category mechanical-design --explain
.\.venv\Scripts\python.exe -m catia_manual catalog
.\.venv\Scripts\python.exe -m catia_manual map --manual asmug_C2 --focus online/cfyugasm_C2/cfyugasmut0301.htm
.\.venv\Scripts\python.exe -m catia_manual relations online/cfyugasm_C2/cfyugasmut0301.htm --type references --type documents_command
.\.venv\Scripts\python.exe -m catia_manual read online/prtug_C2/prtugbt0501.htm
.\.venv\Scripts\python.exe -m catia_manual read online/prtug_C2/prtugbt0501.htm --with-source
.\.venv\Scripts\python.exe -m catia_manual examples online/prtug_C2/prtugbt0501.htm
.\.venv\Scripts\python.exe -m catia_manual stats
.\.venv\Scripts\python.exe -m catia_manual library status
.\.venv\Scripts\python.exe -m catia_manual library manifest
.\.venv\Scripts\python.exe -m catia_manual library verify --database data/index.sqlite3 --root 'D:\Manuals\English'
.\.venv\Scripts\python.exe -m catia_manual library attach --database data/index.sqlite3 --root 'D:\Manuals\English' --destination data/library-copy.sqlite3
.\.venv\Scripts\python.exe -m catia_manual library snapshot --destination data/library-snapshot.sqlite3
.\.venv\Scripts\python.exe -m catia_manual terms --alias '终止曲面' --english 'limiting surface' --module prtug_C2
.\.venv\Scripts\python.exe -m catia_manual resolve-source .cache/source.json
.\.venv\Scripts\python.exe -m catia_manual semantic status
.\.venv\Scripts\python.exe -m catia_manual semantic download
.\.venv\Scripts\python.exe -m catia_manual semantic build
.\.venv\Scripts\python.exe -m catia_manual search '沿旋转轴生成实体' --mode hybrid --explain
```

JSON CLI 和 `core` 不依赖 Qt。调用时使用参数列表，并以 UTF-8 读取 stdout。`open <示例路径>` 默认仅给出启动参数，加 `--execute` 才实际打开。GUI 开发入口支持 `--open <本地文档路径或可映射的文档 URL>`。

目录结构、双语命令词库的开发机提取、真实库校准和新增 CLI 见 [结构化搜索](structured-search.md)。`search --manual` 与 `--category` 包含显式收录的共享页面，`--module` 保留物理目录语义；`locations` 返回同一文章的多个功能目录位置。词库 JSON 已提交并随包分发，构建和运行不访问开发机安装目录。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=.cache/pytest-tmp -q
.\.venv\Scripts\ruff.exe check src tests scripts packaging
.\.venv\Scripts\python.exe scripts/verify_stage2_gui.py
.\.venv\Scripts\python.exe scripts/verify_stage3_gui.py
.\.venv\Scripts\python.exe scripts/verify_stage4_gui.py
.\.venv\Scripts\python.exe scripts/verify_stage5_gui.py
.\.venv\Scripts\python.exe scripts/verify_real_page_translation.py
.\.venv\Scripts\python.exe scripts/verify_stage_a_gui.py
.\.venv\Scripts\python.exe scripts/verify_stage_b_gui.py
.\.venv\Scripts\python.exe scripts/verify_stage_c_gui.py
.\.venv\Scripts\python.exe scripts/verify_stage_c_real.py
.\.venv\Scripts\python.exe scripts/verify_ui_polish.py
.\.venv\Scripts\python.exe scripts/verify_settings_persistence.py
.\.venv\Scripts\python.exe scripts/verify_function_map_gui.py
.\.venv\Scripts\python.exe scripts/verify_function_map_real.py
.\.venv\Scripts\python.exe scripts/verify_relations_gui.py
.\.venv\Scripts\python.exe scripts/verify_relations_real.py
```

原生 GUI 验证会短暂显示窗口，使用独立测试配置和离线模型；示例链接被捕获，不实际启动 CATIA。阶段 2 验证选词 / 句子 / 过期请求 / 链接 / 导航；阶段 3 验证分类 / 设置 / 迁移 / 侧键；阶段 4 验证新按钮 / 菜单 / 底栏。截图和 JSON 报告在忽略提交的 `data` 中。

`scripts/setup_translation.py` 安装英中双向模型；`scripts/build_icons.py` 重建应用位图。二者均属于开发工具。当前环境的固定依赖版本见 `requirements.lock.txt`。

`verify_ui_polish.py` 使用独立构造文档、模拟翻译与浏览器调用，检查中英匹配高亮、原文不变、渐进翻译与来源标记、历史恢复、侧栏零宽度恢复、首次页面切换与最大化状态、窄窗口布局、原文菜单、关于页和透明图标。报告及截图在 `data/ui-polish*`，不加入 CI。

`verify_settings_persistence.py` 使用合成 Key 验证多服务隔离、加密保存、设置重开、清除、取消、测试连接、不可解密时的保留与提示、精简后的窗口、模型目录和窗口状态持久化；不访问网络，不加载模型或启动 CATIA。`test_credentials.py` 另以新进程验证真实 DPAPI 解密。密钥只以密文进入 TOML，不进入模型名称、缓存身份或发行默认值；临时 api_key_override 被保存层过滤。DPAPI 范围及迁移限制见 [Microsoft 文档](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。

功能地图核心在 `core/function_map.py`，界面绘制在 `gui/function_map.py`，工作区与历史适配在 `gui/map_workspace.py`。`map` CLI 输出 GraphSnapshot v1，支持 `--manual` / `--category`、`--focus` 和 12–200 的 `--limit`，不导入 Qt 或推理模型。运行只投影已有目录与文章元数据；结构未整理时保留分类与手册并显示状态，不隐式扫描整库。

`verify_function_map_gui.py` 验证目录上下文、折叠、局部聚焦、正文往返、视口保存、搜索结果定位、共享手册位置以及 200 节点原生绘制，报告在 `data/function-map-gui.json`。`verify_function_map_real.py` 使用结构化搜索验收的隔离数据库，覆盖真实装配、焊接与零件设计，报告在 `data/function-map-real.json`；先运行 `verify_structured_search.py` 生成隔离索引。本机验证均不加入 CI，不启动 CATIA 或发送翻译请求。

页面关系核心为 `core/relations.py`，工作区为 `gui/relation_workspace.py`，绘制和控件复用原生图形框架。CLI `relations` 支持重复 `--type`、重复 `--expand <节点 ID>`、`--manual` 和 12–200 的 `--limit`。GraphSnapshot v1 的 view 分别为 function-map / page-relations。关系边包含类型、方向、原文 SourceLocation、属性、目标、出现次数及可选图片定位信息；同目标不同锚点保留在各条 evidence 中。节点使用文档库内的规范路径或命令标识生成稳定 ID。

命令只识别原文实际出现且能在随包词库核对的 id / name；展开命令时从已有 article_commands 取得候选，并重新核对原文锚点。参考类型使用已有文章角色；没有结构索引时仍可查看显式页面、资源和本页命令，页面分类保守显示为引用。索引导航页、外部地址、脚本及越界链接不构成关系。图片包括正文 img 和显式图片链接。

投影默认 80、最多 200 个节点，每次展开最多 20 条关系、全图最多 400 条边；每条边保存最多 20 处 evidence 并记录总数。循环和交叉引用保留方向，单独标记用于排版的发现边，不把排版父子关系当成原文事实。派生数据每次读取原文生成，relation_view_states 单独保存用户视图，索引重建保留此表。图切换期间抑制中间视口保存，完成历史切换后再保存，避免覆盖前一中心页面的状态。

`verify_relations_gui.py` 以合成文档和模拟外部操作验证关系类型、循环、命令展开、出处、重复图片、示例权限、历史、筛选及重开恢复；`verify_relations_real.py` 使用隔离真实索引验证装配约束和 Creating Pads，报告分别在 `data/relations-gui.json` 与 `data/relations-real.json`。它们不接入 CI，不需要新模型；真实验证前先生成结构化搜索的隔离索引。

两种图的排布与连线路由在 `gui/graph_geometry.py`（不依赖 Qt），原生边图元在 `gui/graph_edge.py`。发现树使用分支跨度排布，增量展开保留已有二维坐标；双向边分配不同端口，先尝试正交走线，受阻时以有数量上限的网格搜索绕开卡片。高密度或用户重叠的卡片不能保证没有交叉；选择/悬停聚焦、交叉底色和标签避让保持可读，不通过删边改变原文关系。

画布隐藏滚动条，并围绕请求的视口动态扩展可导航区域。空白左拖与任意位置中拖平移，节点左拖调整显示位置；滚轮围绕指针缩放。拖动节点时以 16ms 定时器合并关联边的预览更新，释放时重新计算避障路由。位置、选择、视口继续使用既有 GraphViewState；不增加文档编辑、笔记或用户连线表。“整理布局”只重置显示坐标。布局边和语义边继续分离。

本机运行 `python scripts/verify_graph_canvas.py` 验证远距离自由平移、指针缩放、真实鼠标节点拖动、中键平移、位置恢复、整理布局、双向边与标签避让，以及 200 节点的软件绘制。报告及截图为 `data/graph-canvas.json` / `data/graph-canvas.png`，仅供开发验收，不加入 CI。

`verify_stage5_gui.py` 使用构造语料及可控 provider，检查渐进加载、优先级、暂停、失败重试、三种模式、锚点、历史位置和 SQLite 复用。`verify_real_page_translation.py` 使用真实 Creating Pads 和离线模型。`verify_stage_a_gui.py` 使用构造语料与模拟外部操作，检查模式持久化、能力降级、双击、图片预览和状态优先级。以上验证均在本机手动运行，不接入 CI。


B 轮实现见 [文档库与搜索](stage-b.md)，接口定义见 [文档库与来源位置契约](library-contract.md)。`library attach` 只生成已验证的数据库副本，不自动修改 GUI 配置；差异副本须显式加 `--accept-revision`，随后对新库完整更新索引。`verify` 内容不一致退出 2，命令失败退出 1。来源 JSON 可从 `search --explain` 的命中 source 或 `read --with-source` 取得。

`verify_stage_b_gui.py` 验证精确来源、阅读位置、完整副本复用、原库隔离、术语编辑及歧义提示；与其他本机验收一样不接入 CI。Windows 排版验收使用原生 Qt 平台；可设置 `QT_SCALE_FACTOR=1.5` 检查高 DPI。

C 轮实现见 [本机中英混合检索](stage-c.md)。`semantic download` 是显式下载操作，其余命令只读取本地模型；`semantic build` 中断后已提交批次保留。`search --mode` 仅覆盖本次命令，不修改设置。C 轮两份验收脚本需要先在 data/search-models/e5-small 准备模型；real 脚本还需要本机 B33 手册，使用独立副本和数据库，记录受限候选集的固定查询结果，不接入 CI。
