# CatiaManualViewer

CATIA V5 本地帮助文档查看器的 Python 基础原型。核心解析、SQLite 搜索与翻译接口独立于 GUI，也可供 agent 通过 JSON CLI 使用。

完整调查与架构见 [分析与基本设计](docs/analysis-and-design.md)，当前 v0.2 行为、API 配置和验证见 [第二阶段说明](docs/stage2.md)。

## 当前能力

- 读取本机旧 HTML，提取标题、正文块、锚点、资源和示例链接。
- 从静态 Site Map 恢复层级目录，识别框架页和旧导航静态调用。
- SQLite FTS5 英文检索，中文术语扩展与查询翻译，支持模块筛选、标题优先和增量索引。
- PySide6 桌面阅读、图片/表格、目录、搜索与示例列表。
- CATIA COM 会话复用及 CATSTART 回退；CLI 默认只显示启动参数。
- 单词／句子选中即译，350 ms 防抖、请求合并、过期结果过滤与翻译缓存。
- 翻译设置支持大模型兼容 API、LibreTranslate、本机英中双向模型；可测试服务。
- 恢复 `.doc/src` 链接及 API 文档中静态脚本生成的链接；保留页面锚点与模型链接。
- 前进／后退、Alt+←／Alt+→、鼠标侧键与历史下拉菜单，恢复页面滚动位置。

当前本机备用模型已安装。你的真实 API 地址与模型信息尚未配置，可稍后在“翻译设置”中填写并测试。中文搜索查询英文原文，尚未建立整库译文索引。CATIA 的真实模型打开仍需交互联调。

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

只安装核心与开发工具：添加 `-CoreOnly`。默认 bootstrap 包含 `.[gui,catia,translation,offline,dev]` 并安装英中双向模型。仅使用 API 时可添加 `-SkipModels`；离线推理是独立的 `offline` extra。

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
.\.venv\Scripts\python.exe -m catia_manual search '装配约束' --explain
.\.venv\Scripts\python.exe -m catia_manual search '曲面偏置'
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

在阅读器右上角点击“翻译设置”，选择服务、填写地址和模型名，输入本次运行使用的 API Key，然后“测试当前服务”并保存。默认勾选“划词即译”，无需点击翻译按钮。

大模型兼容 API 配置示例（模型名与地址由你的服务提供）：

```toml
[translation]
provider = 'chat-completions'
endpoint = 'https://your-provider.example/v1'
model = 'your-model-name'
api_key_env = 'CATIA_TRANSLATION_API_KEY'
target_language = 'zh-CN'
automatic = true
selection_delay_ms = 350
max_selection_chars = 3000
```

也支持 LibreTranslate-compatible 服务：

```toml
[translation]
provider = 'libretranslate'
target_language = 'zh-CN'
endpoint = 'http://127.0.0.1:5000/translate'
api_key_env = 'CATIA_TRANSLATION_API_KEY'
```

密钥可在设置中输入（只存于当前进程），或从同名环境变量读取，不写进配置文件。配置 API 后，选中文字及需要机器翻译的中文查询会发送到该端点；纯术语查询保持本地。界面修改立即生效，手工修改配置文件后需重启。

本机备用模型安装与单独重建：

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[offline]'
.\.venv\Scripts\python.exe scripts/setup_translation.py
```

选择 `provider = 'local'` 后使用 `data/models` 内的英中双向模型，运行时不下载模型。通用模型对 CAD 语境的翻译有限，侧栏同时显示专业术语。详细边界见 [第二阶段说明](docs/stage2.md)。

## 验证与目录

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=.cache/pytest-tmp -q
.\.venv\Scripts\ruff.exe check src tests scripts
.\.venv\Scripts\python.exe scripts/smoke_gui.py
.\.venv\Scripts\python.exe scripts/verify_stage2_gui.py
```

GUI smoke test 需要已有索引，使用 offscreen Qt 渲染并写入 `data/gui-smoke.png`。第二阶段 GUI 验证在 Windows 桌面短暂显示阅读器，检查自动选词、句子翻译、旧请求过滤、真实链接和前后导航，写入 `data/stage2-gui.png`；它使用本机模型并捕获示例打开请求，不访问远程 API 或启动 CATIA。第一阶段记录见 [本机验证](docs/verification.md)。

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

