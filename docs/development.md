# 开发与构建

使用 Python 3.12–3.14；Windows Nuitka/MinGW64 构建使用 3.12，CI 使用 Windows 2022 / MSVC。

## 环境

```powershell
.\scripts\bootstrap.ps1 -PythonExe 'C:\path\to\python.exe' -SkipModels
.\.venv\Scripts\python.exe -m pip install -e '.[gui,catia,translation,offline,search,agent,dev,packaging]'
.\.venv\Scripts\python.exe -m catia_manual gui
```

`config.example.toml` 是通用开发模板，个人修改写入忽略提交的 `config.local.toml`。首次启动在设置中选择文档目录。无需真实文档即可运行核心测试；真实 CATIA 和网络翻译操作不属于自动测试。

## 核心 CLI

```powershell
python -m catia_manual doctor
python -m catia_manual index
python -m catia_manual search '相合约束' --explain
python -m catia_manual read online/prtug_C2/prtugbt0501.htm --with-source
python -m catia_manual map --manual asmug_C2
python -m catia_manual relations online/prtug_C2/prtugbt0501.htm
python -m catia_manual library status
```

各命令的参数见 `--help`。核心导入不加载 Qt 或推理运行时；模型仅在用户显式使用相应功能时加载。`semantic download` 下载模型，`semantic build` 建立可续建的向量扩展；`library snapshot/verify/attach` 用于安全复用文档索引。

## 本地验证

```powershell
python -m pytest -q --basetemp=.cache/pytest-tmp
ruff check src scripts tests packaging
```

保留关键的解析、资源边界、检索、迁移、缓存、图与发布测试。原生界面验收可运行 `scripts/verify_function_map_gui.py`、`scripts/verify_relations_gui.py`、`scripts/verify_graph_canvas.py` 和 `scripts/verify_transfer_gui.py`；使用合成文档，不启动 CATIA 或调用翻译 API。报告写入忽略提交的 `data`。高 DPI 可设置 `QT_SCALE_FACTOR=1.5`；窗口交互检查使用原生 Qt 平台。

Agent 验收使用 `scripts/verify_agent.py`（官方 MCP 客户端、真实 TCP、认证与只读校验）及 `scripts/verify_agent_gui.py`（界面启停、加密保存、库授权与进程退出）。前者可加 `--real` 只读检查本机配置的真实手册。Windows 上网络及 asyncio 验证需在允许本机 socket 的原生环境运行；不需要外部网络或云服务。使用方式见 [Agent 接入](agent.md)。这些验证不加入 CI。

便携包构建后，使用 `python scripts/verify_agent.py --exe '便携目录/CatiaManualViewer.exe'` 验证编译版服务入口；客户端从独立目录连接真实 TCP，不依赖编译进程的 Python 环境。

MCP 验收默认使用免令牌模式；加 `--require-token` 验证可选认证及令牌轮换。
Windows 开发机可加 `--wsl`，使用默认 WSL 发行版的 Python 标准库验证跨系统的初始化、搜索与原文读取，无需在 WSL 安装额外依赖。

## 维护工具

- `scripts/build_icons.py`：从随库 SVG 生成 PNG/ICO。
- `scripts/build_terminology.py`：开发机提取随包双语词库，不是运行时依赖。
- `scripts/setup_translation.py`：按 `packaging/models.lock.json` 安装并校验离线翻译模型。

## 构建

```powershell
python scripts/setup_translation.py --model-dir .cache/offline-models --lock packaging/models.lock.json
python scripts/build_portable.py --compiler msvc --models .cache/offline-models
```

构建只复制运行程序、公开默认配置、模型与许可证，不复制个人设置或索引。本机构建默认执行 EXE 运行诊断；CI 跳过 EXE 启动，仅构建和校验 ZIP。当前版本和发布操作见 [CI 与发布](ci-and-release.md)。
