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
```

原生 GUI 验证会短暂显示窗口，使用独立测试配置和离线模型；示例链接被捕获，不实际启动 CATIA。阶段 2 验证选词 / 句子 / 过期请求 / 链接 / 导航；阶段 3 验证分类 / 设置 / 迁移 / 侧键；阶段 4 验证新按钮 / 菜单 / 底栏。截图和 JSON 报告在忽略提交的 `data` 中。

`scripts/setup_translation.py` 安装英中双向模型；`scripts/build_icons.py` 重建应用位图。二者均属于开发工具。当前环境的固定依赖版本见 `requirements.lock.txt`。

`verify_stage5_gui.py` 使用构造语料及可控 provider，检查渐进加载、优先级、暂停、失败重试、三种模式、锚点、历史位置和 SQLite 复用。`verify_real_page_translation.py` 使用真实 Creating Pads 和离线模型。`verify_stage_a_gui.py` 使用构造语料与模拟外部操作，检查模式持久化、能力降级、双击、图片预览和状态优先级。以上验证均在本机手动运行，不接入 CI。


B 轮实现见 [文档库与搜索](stage-b.md)，接口定义见 [文档库与来源位置契约](library-contract.md)。`library attach` 只生成已验证的数据库副本，不自动修改 GUI 配置；差异副本须显式加 `--accept-revision`，随后对新库完整更新索引。`verify` 内容不一致退出 2，命令失败退出 1。来源 JSON 可从 `search --explain` 的命中 source 或 `read --with-source` 取得。

`verify_stage_b_gui.py` 验证精确来源、阅读位置、完整副本复用、原库隔离、术语编辑及歧义提示；与其他本机验收一样不接入 CI。Windows 排版验收使用原生 Qt 平台；可设置 `QT_SCALE_FACTOR=1.5` 检查高 DPI。

C 轮实现见 [本机中英混合检索](stage-c.md)。`semantic download` 是显式下载操作，其余命令只读取本地模型；`semantic build` 中断后已提交批次保留。`search --mode` 仅覆盖本次命令，不修改设置。C 轮两份验收脚本需要先在 data/search-models/e5-small 准备模型；real 脚本还需要本机 B33 手册，使用独立副本和数据库，记录受限候选集的固定查询结果，不接入 CI。
