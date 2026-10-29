# 第二阶段：即时翻译、原文链接、中文查询与导航

日期：2026-10-29；版本：0.2.0。核心仍为独立 Python API，Qt 是一个适配层。

当前 v0.3 的功能分类、全局设置与迁移方式见 [第三阶段说明](stage3.md)。

## 使用方式

启动 `python -m catia_manual gui`，双击单词或拖选句子，选区稳定约 350 ms 后，右侧自动显示翻译。翻译入口保留右键重试；常规使用不需要按钮。

前进／后退支持工具栏、Alt+←／Alt+→ 和鼠标侧键。按钮旁的下拉菜单可以跳到最近的历史项。历史包含页面、命名锚点、标题和滚动位置，点击示例不会污染浏览历史；后退后访问新位置会清除前进分支。最多保留 200 项。

页面解析与规范化放到后台；最近 8 个页面保留内存缓存。异步导航按请求序号过滤旧结果，避免慢页面盖掉后来选择的页面。

## 翻译 API 配置

按本轮用户选择，真实翻译服务以用户已有 API 为主；其地址、模型和密钥尚未提供，因此本轮未连接真实远程 API。离线模型保留为本机备用及交互验证途径，不代表已经替用户配置了远程账号。

设置界面提供：

- 大模型 API：OpenAI-compatible Chat Completions 协议，用户指定地址、模型和 key。
- LibreTranslate：完整 `/translate` 地址，支持英文→中文与中文→英文。
- 本机离线模型：CPU CTranslate2 + SentencePiece，加载官方 Argos 模型数据。
- 仅术语辅助：不调用模型或网络。

大模型模式的地址可以是 `https://provider.example/v1`，也可以是完整的 `/chat/completions` 地址。模型名必须由服务提供方给出，不预设具体付费模型。适配器发送 `model`、`messages`，读取 `choices[0].message.content`；遇到空结果、拒绝或长度截断会显示错误，不缓存为正常翻译。请求结构依据 [OpenAI 官方 Chat Completions 文档](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)，第三方服务的实际兼容性需待配置后验证。

API key 可在密码框中填写，仅存于当前进程的内存凭据表，不放入子进程继承的环境；关闭应用后需重新输入。也可以自行提供配置指定的环境变量。key 不写入 TOML、数据库或日志。设置保存到 `config.local.toml`，保留文档与 CATIA 路径配置；原始 example 文件不被设置界面改写。

“测试当前服务”会翻译固定的示例句子，只有明确点击测试才发起该请求。选中文字自动翻译开启后，每个稳定选区会自动调用配置的服务；中文查询只有未被术语表覆盖的部分需要机器翻译。

## 自动翻译调度

Qt 的 `selectionChanged` 触发防抖计时器；选择变空、切页、切换服务或关闭自动翻译都会使当前选区的请求序号失效。

同一时间最多运行一个选区翻译，期间新选区只保留最新的待处理项。已经发出的 HTTP 请求不强行中断，但结果只有在序号仍一致时才显示，过期结果不会覆盖新选择。失败显示在翻译侧栏，不弹出阻塞划词操作的对话框；右键可重试。

默认自动翻译上限为 3,000 字符，可在设置中调整到 10,000。核心服务也限制单次长度为 10,000。成功结果仍按 provider、模型/模型版本、语言和原文缓存；服务地址或模型切换后不会复用另一服务的结果。

本机模型：

| 方向 | Argos 包版本 | 下载大小 |
| --- | --- | ---: |
| English → Chinese | 1.9 | 70,743,021 字节 |
| Chinese → English | 1.9 | 74,481,402 字节 |

下载来源与 SHA-256 记录在本机 `data/models/manifest.json`。安装脚本从 [Argos 官方模型索引](https://github.com/argosopentech/argospm-index) 获取模型；运行时不自动下载任何内容。包路径经过检查，仅提取推理模型、分词器、元数据及可用的说明/许可证文件。

离线模型是通用机器翻译，CAD 句子可能译偏。例如 pad 可能被通用模型理解为“垫”。单词查询优先使用 CATIA 术语表，支持 Pads、Sketches 等复数；句子结果同时附专业术语释义。本轮没有把离线模型的质量宣称为专业文档翻译质量。

## 内置链接兼容

本轮范围按用户澄清为阅读器内的文档、锚点和示例跳转。

1. 普通相对 HTML、图片、模型文件链接继续规范化为文档根目录内的路径。
2. 把 `.../module.doc/src/file.htm` 的旧开发期路径转换为实际 `online/module_C2/file.htm`。不存在的目标保留为缺失，不访问根目录外文件。
3. 读取 `interfaces/_index/jslink.js` 的静态对象与别名表，恢复 `activateLink`、`activateLinkAnchor` 生成的交叉链接。
4. 读取当前接口页面和 `jsFwLink.js` 的静态数组，恢复接口参考的 Previous / Next。
5. 保留同页和跨页的命名锚点，缓存文件名不参与文档身份判断。
6. 示例链接交给现有 CATIA adapter；外部 HTTP(S) 链接交给系统浏览器；受支持的 PDF/TXT 本地附件可交给系统应用。

原文脚本不执行。应用仅在 Qt 的隔离 `ApplicationWorld` 中运行自己的锚点定位与滚动恢复代码；正文的 MainWorld JavaScript 保持禁用。[Qt 官方设置说明](https://doc.qt.io/qtforpython-6/PySide6/QtWebEngineCore/QWebEngineSettings.html)区分了该设置对 MainWorld 的作用，[QWebEnginePage 文档](https://doc.qt.io/qtforpython-6/PySide6/QtWebEngineCore/QWebEnginePage.html)说明了隔离脚本世界。

新增链接恢复也会改善核心解析的正文和可供 agent 使用的 links，不仅作用于 GUI。解析器版本为 2；完整索引成功后记录版本，检测到旧版索引时自动重解析。模型和外部应用打开仍是明确的用户点击操作。

## 中文搜索实现

本轮选择“术语扩展 + 查询翻译 + 现有英文 FTS5”，不要求先翻译整套 1.5 万页文档：

```text
中文查询
  → 按最长词匹配 CATIA 术语
  → 同一概念的英文候选 OR，不同概念 AND
  → 去掉“如何”等常见问句连接词
  → 未覆盖中文用已配置的服务译为英文
  → 对英文原文 FTS5 检索
  → 返回原文页面、摘要和实际查询展开
```

示例：

| 中文 | 检索概念 | 本机首条结果 |
| --- | --- | --- |
| 创建凸台 | pad + create | Creating a Pad |
| 装配约束 | assembly + constraint | Analyzing Assembly Constraints |
| 曲面偏置 | surface + offset | Creating Offset Surfaces |
| 圆角半径 | fillet + radius | Creating Fillet Radius Dimensions |
| 曲率梳 | Curvature comb（查询翻译） | Analyzing a Curve by Porcupine Analysis |

词典映射可离线使用。无法翻译的未知中文不会被静默丢弃并产生无关的部分查询，GUI/CLI 会返回说明。CLI 增加 `search '装配约束' --explain`，输出 QueryPlan；直接 `ManualIndex.search()` 支持术语映射，`SearchService` 才会执行需要翻译服务的扩展。

局限：这是词法检索的跨语言入口，排名仍受英文原文用词与查询翻译影响。例如“如何隐藏零件”能转成 `part + hide`，但首条结果可能是网格显示隐藏，而非目标零件操作。它不是中文全文索引或问答系统。后续可补领域词典与中文译文标题索引；如果需要跨语言语义检索，再单独评估多语言 embedding，避免无依据地增加向量库和大模型依赖。

## 验证结果

- 34 项 pytest 测试通过，覆盖原有能力、新历史状态机、中文查询、翻译 API 模拟契约、静态脚本链接恢复、配置保存以及索引更新期间的并发只读查询。
- 真实 Windows Qt 界面 12 项检查通过：单词和句子自动翻译、旧结果过滤、文档跳转、前进后退与滚动恢复、同页锚点、分支截断、示例路由和中文检索。
- 已用真实 `Documents` 页面点击恢复的 `PartDocument` 交叉链接。
- 使用新版解析器重建 15,334 页索引，全部成功；SQLite 和 FTS5 完整性检查均为 ok。
- 结构化索引中的 document 链接由 262,708 增至 278,388；blocked 从 2,986 减至 161。剩余项包括无法解析的脚本和原文缺失/不支持的路径，不能据此承诺全部旧页面完全兼容。
- 真实远程 API 尚未连接。大模型兼容 API 与 LibreTranslate 的协议契约使用模拟 HTTP 验证；模型质量与响应速度需待用户提供配置后测量。
- 示例点击路由在 GUI 测试中被捕获验证，没有实际启动 CATIA，真实模型加载仍需联调。

本机输出：`data/stage2-gui.png`、`data/stage2-gui-verification.json`、`data/stage2-core-verification.json`、`data/stage2-index.log`。这些数据不提交仓库。
