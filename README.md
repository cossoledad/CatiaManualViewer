# CatiaManualViewer

CATIA V5 本地帮助文档查看器的 Python 基础原型。核心解析、SQLite 搜索与翻译接口独立于 GUI，也可供 agent 通过 JSON CLI 使用。

完整调查、架构与迭代边界见 [分析与基本设计](docs/analysis-and-design.md)。

## 当前能力

- 读取本机旧 HTML，提取标题、正文块、锚点、资源和示例链接。
- 从静态 Site Map 恢复层级目录，识别框架页和旧导航静态调用。
- SQLite FTS5 英文搜索、模块筛选、标题优先、摘要与增量索引。
- PySide6 桌面阅读、图片/表格、目录、搜索与示例列表。
- CATIA COM 会话复用及 CATSTART 回退；CLI 默认只显示启动参数。
- 选中文字后的离线术语辅助、翻译缓存和可配置 LibreTranslate adapter。

默认术语辅助不是整句翻译；中文全文翻译和中文搜索待下一阶段实现。CATIA 的真实模型打开仍需交互联调。

## 本机启动

当前项目的 `.venv` 已安装完整依赖，无需激活环境：

```powershell
cd C:\Code\CatiaManualViewer
.\.venv\Scripts\python.exe -m catia_manual doctor
.\.venv\Scripts\python.exe -m catia_manual gui
```

也可运行无终端窗口的启动脚本：

```powershell
.\scripts\start.ps1
```

本机默认路径已在 `config.example.toml` 配好。个人设置放入忽略提交的 `config.local.toml`，或用 `--config <路径>` 指定；相对数据库路径以配置文件目录为基准。

## 新环境安装

使用实际 Python 3.12–3.14 x64 路径，不依赖 WindowsApps 别名。当前环境测试的是 3.12；可选范围的其他版本需另做兼容测试。

```powershell
.\scripts\bootstrap.ps1 -PythonExe 'C:\path\to\Python312\python.exe'
# 已有 .venv 时可直接运行
.\scripts\bootstrap.ps1
```

只安装核心与开发工具：添加 `-CoreOnly`。GUI、CATIA 与 HTTP 翻译的依赖是独立 extras：`.[gui,catia,translation,dev]`。

当前 Windows / Python 3.12 的精确版本记录在 `requirements.lock.txt`。需要重现时：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
```

锁定文件用于重现本机开发环境，不是跨平台或附带哈希的供应链锁。

## CLI 与 agent 使用

所有正常结果以 JSON 输出；索引进度到 stderr，失败返回非零退出码。

```powershell
# 首次全库索引；后续自动跳过未变化文件
.\.venv\Scripts\python.exe -m catia_manual index
# 模块范围和有限扫描；--force 强制重新解析
.\.venv\Scripts\python.exe -m catia_manual index --module prtug_C2
.\.venv\Scripts\python.exe -m catia_manual search 'creating pads' --module prtug_C2
.\.venv\Scripts\python.exe -m catia_manual read online/prtug_C2/prtugbt0501.htm
.\.venv\Scripts\python.exe -m catia_manual toc online/prtug_C2/prtugsm.htm
.\.venv\Scripts\python.exe -m catia_manual examples online/prtug_C2/prtugbt0501.htm
.\.venv\Scripts\python.exe -m catia_manual stats

# 查看命令；加 --execute 才实际交给 CATIA
.\.venv\Scripts\python.exe -m catia_manual open online/prtug_C2/samples/Pad1.CATPart
.\.venv\Scripts\python.exe -m catia_manual translate 'Creating pads from a sketch'
```

面向 agent 的调用应始终用参数列表，并以 UTF-8 读取 stdout。必要时设置 `PYTHONIOENCODING=utf-8`。读取结果包含 `path`、`content_hash` 和块 ID，可以回溯原文。

直接 Python 调用也不导入 Qt：

```python
from catia_manual.config import load_settings
from catia_manual.core.index import ManualIndex
from catia_manual.core.parser import ManualParser

settings = load_settings()
document = ManualParser(settings.root).parse('online/prtug_C2/prtugbt0501.htm')
with ManualIndex(settings.database, settings.root) as index:
    hits = index.search('pad', module='prtug_C2')
```

## 配置整句翻译

先运行你自己的 LibreTranslate-compatible 服务，再在 `config.local.toml` 中替换配置：

```toml
[translation]
provider = 'libretranslate'
target_language = 'zh-CN'
endpoint = 'http://127.0.0.1:5000/translate'
api_key_env = 'CATIA_TRANSLATION_API_KEY'
```

需要 key 的服务使用同名环境变量提供，不把 key 写进配置文件。当前没有自动部署翻译引擎；设置远程 endpoint 后，执行翻译会发送所选文字到该端点。修改配置后重启阅读器。

## 验证与目录

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=.cache/pytest-tmp -q
.\.venv\Scripts\ruff.exe check src tests scripts/smoke_gui.py
.\.venv\Scripts\python.exe scripts/smoke_gui.py
```

GUI smoke test 需要已有索引，使用 offscreen Qt 渲染并写入 `data/gui-smoke.png`，不启动 CATIA。真实搜索和索引记录见 [本机验证](docs/verification.md)。

```text
src/catia_manual/
  core/             解析、路径、索引、翻译和数据模型
  integrations/     CATIA adapter
  gui/              可选 Qt 桌面界面
  cli.py            JSON 命令行入口
docs/               调查、设计和验证记录
tests/              人工构造语料与边界验证，不携带 CATIA 原文
scripts/            环境安装、启动与 GUI smoke test
data/               本机索引、翻译缓存和截图，不提交
```

