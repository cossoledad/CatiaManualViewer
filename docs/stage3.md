# 第三阶段：功能分类、全局设置与应用界面

日期：2026-10-29；版本：0.3.0。应用名称统一为 **Catia Manual Viewer**。

## 功能分类与搜索范围

导航以安装文档的 `online/CATIA_P3_TOC.xml` 为依据，与原始 `CATIA_P3_default.htm` 首页的功能分类对应。它不再把目录名称如 `prtug_C2` 直接当作界面分类。

| 分类 | 英文名称 | 本机手册数 |
| --- | --- | ---: |
| 基础与平台 | Infrastructure | 26 |
| 机械设计 | Mechanical Design | 26 |
| 曲面与造型 | Shape Design & Styling | 12 |
| 分析与仿真 | Analysis | 6 |
| 设备与系统工程 | Equipment & Systems Engineering | 25 |
| 数控加工 | Machining | 15 |
| 产品综合与知识工程 | Product Synthesis | 20 |

功能首页展示分类卡片，进入分类后展示手册卡片。常用手册使用中文名称，保留原文标题；未收录中文名称的手册显示官方英文名称。每本手册内部仍按 Site Map 展开章节，不把原始 HTML 文件清单铺在导航树上。

分类数据由独立的 `core/catalog.py` 提供，CLI、GUI 和未来 agent 均可读取。若文档副本没有 TOC.xml，尝试从旧首页静态分类与手册标题恢复；再无法恢复时回退到有 Site Map 的参考手册。缺少的手册不作为可打开项显示。

选择分类会联动搜索范围；选择手册后可以在该手册中搜索，也可从顶部切回全部功能或其他分类。范围约束直接加入 SQLite 查询，在排序和结果数量限制之前执行，不采用“先全库查前 80 条再过滤”。

自动化与 API 手册的范围包含对应的 CAA 和 interfaces 内容。公共页面仍可通过全库搜索与交叉链接访问。

```powershell
.\.venv\Scripts\python.exe -m catia_manual catalog
.\.venv\Scripts\python.exe -m catia_manual search '圆角' --category mechanical-design
.\.venv\Scripts\python.exe -m catia_manual search '曲面偏置' --category '曲面与造型'
```

## 全局设置

右上角设置按钮或 Ctrl+, 打开设置。设置分为三个页面：

- **文档与 CATIA**：文档根目录、索引缓存目录、离线模型目录、CATIA 启动器、环境目录和环境名称。
- **翻译**：翻译源、服务参数、密钥、自动翻译和选区限制。
- **阅读与界面**：界面字号、阅读缩放、是否显示阅读侧栏、是否记住窗口大小。

目录选择支持直接选中含 `online` 的根目录，也可识别其 `English` 子目录。阅读文档不要求 CATIA 安装路径有效；示例打开时才使用 CATIA 配置。

保存后立即生效。更换文档根目录会重建功能目录并清除旧浏览历史、结果与页面缓存。已经运行的任务保留启动时的配置快照，旧任务的结果不能覆盖新库界面。

配置使用 TOML 保存，支持嵌套的多翻译源参数，并保留未知配置节与注释。应用内可相对表示的数据、缓存和模型路径会写成相对于配置文件的路径。密钥现按服务以 Windows 用户级 DPAPI 加密保存于 api_key_encrypted，重启恢复；原有环境变量方式保留。取消设置不提交新密钥，测试服务使用临时凭据。更换电脑或用户导致无法解密时，界面提示重新填写，未编辑的旧密文不会因其他设置保存而被清空。

如果指定的数据目录不可写，设置会提示更换目录。应用源码目录应位于可写位置，以保存个人配置；当前交付是 Python 应用，尚未制作独立 Windows 安装包。

## 翻译源默认值

| 来源 | 默认地址或资源 | 密钥需求 |
| --- | --- | --- |
| 本机离线模型 | `data/models`，英中双向 Argos 模型 | 无 |
| 离线术语表 | 程序内 CATIA 术语表 | 无 |
| MyMemory | `https://api.mymemory.translated.net/get` | 默认无需，可选 key |
| DeepL API Free | `https://api-free.deepl.com/v2/translate` | 需要 DeepL API key |
| LibreTranslate | `http://127.0.0.1:5000/translate` | 取决于自建服务 |
| 大语言模型兼容 API | 地址与模型名留空，由用户填写 | 取决于服务 |

各来源分别记住地址、模型及密钥环境变量名称，切换不会用另一来源的地址覆盖它。选择本机源时隐藏无关的地址和密钥字段，非大模型源不显示模型名称字段。

MyMemory 请求按 UTF-8 字节限制拆分，检查服务返回状态并处理实体编码。DeepL 使用其规定的鉴权头及翻译接口。预设地址不等于已经创建账号、部署服务或保证额度可用；远程服务只有在用户选择后执行翻译，或明确点击测试时才访问。

协议依据：[MyMemory 技术说明](https://mymemory.translated.net/doc/spec.php)、[DeepL 请求格式](https://developers.deepl.com/api-reference/translate/request-translation)、[DeepL 鉴权与 Free/Pro 地址](https://developers.deepl.com/docs/getting-started/auth)。本轮远程接口使用模拟 HTTP 验证，没有发送真实帮助文档给这些服务。

## 界面与图标

窗口和应用名称均为 Catia Manual Viewer。新增原创 SVG 图标，并提供 PNG、ICO 资源，应用于窗口、任务栏和项目内的启动快捷方式。Windows 进程设置固定的应用标识，资源同时包含在 Python wheel 中。

主界面采用统一的浅色与青绿色样式。启动时显示功能首页；阅读器打开后才显示翻译和示例侧栏。索引更新移入设置菜单，原始文件路径和查询展开不再长期占据状态栏，必要信息保留在悬停提示或实际错误中。没有示例的页面隐藏空示例区。

阅读页保留原始图文内容、表格和链接，统一基础排版，移除旧表格的固定高度，避免由历史布局产生的大段空白。

## 鼠标侧键修复

原问题来自两个导航系统同时参与：应用在侧键按下时执行自建历史后退，但 WebEngine 随后收到弹起事件，又触发浏览器自己的历史导航。

现在完整拦截侧键的按下、弹起与双击事件，弹起不再传入 WebEngine；每次按下仅执行一次应用导航。WebEngine 的原生 BackForward 导航请求也被拒绝，应用历史成为唯一的页面导航来源。页面、锚点和功能首页均参与同一份历史。

真实 Qt 回归分别发送了后退键按下、等待页面完成、再发送弹起；对前进键重复同样过程。两者在弹起后均保持目标页面与实际 WebEngine URL 不变。

## 迁移到另一台电脑

1. 复制项目源码和图标资源，放在可写目录。不要把旧 `.venv` 直接作为新电脑环境使用。
2. 在新电脑使用真实的 Python 3.12–3.14 路径运行 bootstrap，重新建立虚拟环境。仅使用在线翻译时可跳过模型下载：

   ```powershell
   .\scripts\bootstrap.ps1 -PythonExe 'C:\path\to\python.exe' -SkipModels
   ```

3. 打开项目内的 `Catia Manual Viewer.lnk`，在设置中选择本机文档副本。若旧目录不存在，首页提供文档目录选择入口。
4. 首次搜索新库时点击“建立搜索索引”，或从设置菜单更新索引。若需要离线翻译，可复制 `data/models`，或运行 `scripts/setup_translation.py`。

索引保留与原文根目录的绑定。文档目录变化时自动选择独立的索引文件，不静默重新绑定或覆盖旧库；同一份文档复制到新路径后也按新库建索引。切回原路径仍可使用原索引。

快捷方式包含本机路径，迁移后由 bootstrap 重新生成，亦可单独执行：

```powershell
.\scripts\create_shortcut.ps1
```

## 验证记录

- 41 项 pytest 测试通过：包括分类解析、分类 SQL 约束、路径迁移、原索引保护、多源配置、默认值和 HTTP 协议。
- 18 项第三阶段真实 GUI 检查通过：分类首页、范围搜索、手册打开、侧键按下/弹起、翻译源切换、外观生效、文档副本迁移、独立索引建立与切回原库。
- 使用只有一个 HTML 和 TOC.xml 的文档副本验证迁移阅读，不依赖该副本旁存在 CATIA 安装目录。
- 第二阶段的自动选词、句子翻译、过期结果过滤、锚点和示例路由共 12 项 GUI 回归也全部通过。

本机截图：`data/stage3-home.png`、`data/stage3-category.png`、`data/stage3-reader.png`、`data/stage3-global-settings.png`。GUI 结果：`data/stage3-gui-verification.json`。测试使用独立配置，不改变当前用户的库与翻译配置，也不启动 CATIA。
