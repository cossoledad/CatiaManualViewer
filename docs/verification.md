# 本机验证记录

日期：2026-10-29，Windows x64；对应当前基础原型。本记录区分真实数据验证与模拟接口验证。

## 环境

- 项目虚拟环境：`C:\Code\CatiaManualViewer\.venv`。
- Python 3.12.14、SQLite 3.53.1，FTS5 创建与查询正常。
- Python 基础解释器来自本机 Codex runtime：`C:\Users\ganjb\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`。虚拟环境仍依赖该基础解释器；若缓存被移除，可用独立安装的 Python 3.12–3.14 通过 bootstrap 脚本重建。
- PySide6 6.12.0、BeautifulSoup 4.15.0、lxml 6.1.3、pywin32 312、httpx 0.28.1；精确版本见 `requirements.lock.txt`。
- `doctor` 确认文档根目录、入口、CATSTART 和 CATEnv 环境文件均存在。
- `pip check`：No broken requirements found。

WindowsApps 的 `python`/`py` 别名在当前受限执行环境中无法启动；通过真实路径发现的用户 Python 是 3.15.2。本轮没有修改系统 PATH 或用户已有 Python。

## 真实文档与索引

首次先索引 Part Design 的 141 个页面，随后全库扫描：

```json
{
  "discovered": 15334,
  "updated": 15193,
  "unchanged": 141,
  "deleted": 0,
  "errors": []
}
```

再执行完整增量扫描：15,334 个文件全部识别为未变化，更新 0、删除 0、错误 0。一次测量约 4.495 秒，包含文件遍历与 stat。

索引包含 14,225 个 article、261 个 frameset、848 个 navigation；默认搜索仅返回 article。Part Design 的静态 Site Map 提取到 267 个目录项，保留原始重复项、跨页引用与缩进层级。

数据库文件约 231 MiB，实际路径 `data/manuals.sqlite3`。原始文档未复制进仓库。SQLite `integrity_check` 返回 `ok`，FTS5 外部内容一致性检查通过。

单次本机查询观测，非统计基准，查询前索引已打开：

| 查询 | 范围 | 耗时 | 首条结果 |
| --- | --- | ---: | --- |
| creating pads | prtug_C2 | 4.018 ms | Creating Pads |
| fillet | 全库 | 6.041 ms | Fillet |
| assembly constraints | 全库 | 7.797 ms | Analyzing Assembly Constraints |
| Documents Open | interfaces | 22.554 ms | Documents (Object) |

详细本机结果写入忽略提交的 `data/verification.json`。索引过程对一类含 XML 声明的 HTML 发出了 BeautifulSoup 提示；解析仍完成，没有转换为 XML DOM，也没有网络读取。

## 自动化检查

`pytest`：20 passed。覆盖：

- 正文块、表格中的直接文本与嵌套段落、旧编码、框架识别。
- 静态脚本链接与渲染重写、图片路径、锚点、目录缩进。
- 目录穿越与网络共享路径识别、可打开示例白名单。
- 增量跳过、内容修改、FTS 同步删除、有限扫描不误删、失败扫描不清理旧记录。
- 数据库根目录绑定，配置相对路径与安装包默认设置。
- 翻译缓存按提供方、目标语言、原文隔离；LibreTranslate 模拟 HTTP 契约。
- CATIA 启动参数中的空格路径，以及模拟 COM 会话的 Documents.Open 调用。
- 核心模块导入不加载 PySide6。

`ruff check` 通过。Python wheel 与 sdist 构建通过；它们是 Python 分发产物，不是 Windows 独立安装包。

## GUI

`scripts/smoke_gui.py` 使用实际 PySide6 + Qt WebEngine 加载本机 `Creating Pads`，检查正文文字、异步搜索结果和模型链接，生成 `data/gui-smoke.png`。

- 页面实际文字包含 Creating Pads，图片和表格经截图人工检查可见。
- 搜索 `creating pads` 返回 80 个结果（当前显示上限），精确标题首位。
- 本页示例列表包含真实的 `Pad1.CATPart`。
- 最大的原始页面 `online/interfaces/_index/CAAMethodIdx.htm` 为 2,320,096 字节；采用本地渲染缓存加载成功，读取到 702,830 个正文字符。
- 真实 Windows Qt 平台验证命名锚点 `RI-330128A`，页面滚动到 y=969；通过阅读器的焦点控件选择 6,189 个字符后，异步术语翻译成功显示在侧栏。结果记录于 `data/gui-navigation-verification.json`。离屏平台未能模拟输入焦点，因此该项使用真实 Windows 平台验证。

在受限进程环境内，Chromium 原生进程曾异常退出；在普通 Windows 进程权限下完成了验证。离屏平台不会自动枚举系统字体，因此 smoke 脚本显式加载系统字体并采用软件渲染。离屏日志仍存在 GPU context fallback 提示；页面加载与文字、图片验证通过。实际桌面模式使用 Qt 默认渲染设置。

大页面的解析与渲染 HTML 生成目前在 UI 线程执行，可能造成短暂等待；后台索引、查询与翻译不会共享 SQLite connection。后续可把阅读预处理异步化。

## 尚未验证或尚未提供

- 未实际启动 CATIA 或打开模型；COM 与 CATSTART 流程经过参数和模拟验证，尚需在真实许可证与会话下联调。示例依赖图、已有文件重复打开、CATIA 权限级别差异也需覆盖。
- 未部署或调用真实 LibreTranslate 服务。离线术语表只做术语释义；整句翻译质量、中文全文翻译、中文查询均未完成。
- 未逐页目视检查 15,334 个页面；复杂动态 JavaScript 导航、视频、插件与所有 frames 组合不能由全库解析成功推导为完整 GUI 兼容。
- 未制作可独立安装的 EXE/MSI，未验证其他 Python/Qt 组合。
