# GitHub CI 与首次 Release

本轮只添加仓库中的工作流和脚本，没有推送、创建远端标签、上传制品或触发远端构建。首次公开版本沿用 `pyproject.toml` 中的 **0.5.0**，标签为 **v0.5.0**。

## 触发方式

| 入口 | 行为 |
| --- | --- |
| 分支 push / Pull Request | 运行 CI：核心测试、Ruff 和构造语料的无界面阅读器测试 |
| Actions → Release → Run workflow | 校验源码并构建 Windows 便携包，提供 Actions 下载制品；不创建 Release |
| push `vX.Y.Z` 标签 | 标签必须匹配项目版本；检查、构建及制品校验成功后发布 GitHub Release |

先将这些文件提交并推送到默认分支，可使用手动入口试构建。准备发布时，在包含这些改动的提交上执行：

```powershell
git tag v0.5.0
git push origin v0.5.0
```

这里的命令由维护者执行。未来版本先修改 `pyproject.toml`，并添加同名的 `docs/releases/vX.Y.Z.md`，再创建对应标签。当前只接受正式的三段数字版本，不自动创建标签或处理预发布版本。

## 工作流分工

`.github/workflows/ci.yml` 可独立触发，也供 Release 以 `workflow_call` 复用：

- Windows 2022，Python 3.12 / 3.14：安装核心、HTTP mock 测试和 Windows COM adapter 依赖，运行 Ruff 与 pytest。COM 测试使用 mock，不连接 CATIA。
- 单独的 Python 3.12 阅读器任务使用 Qt offscreen 和 `verify_stage5_gui.py`，在临时目录生成文章、图片、链接和模拟翻译结果；不需要 CATIA、原版手册、离线模型或翻译 API。
- 保留 JUnit、阅读器 JSON 与截图作为 Actions 制品，保留期 14 天。

`.github/workflows/release.yml` 先校验版本及发布说明，并复用上述 CI。构建在 Windows 2022 / Python 3.12 x64 上使用预装 MSVC 和固定版本 Nuitka 4.3.1，最多 2 个编译任务，超时 90 分钟。主流程权限为 `contents: read`；只有最终 publish 任务请求 `contents: write`，使用内置 `GITHUB_TOKEN`，无需额外 PAT。

官方 Actions 固定到完整 commit SHA，并标注版本号。Nuitka 缓存只保存依赖扫描和编译缓存，不保存上次的发行目录；模型缓存按锁文件哈希隔离。相关语义见 [GitHub 工作流触发文档](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)和 [gh release create](https://cli.github.com/manual/gh_release_create)。

## 依赖与模型

`requirements.lock.txt` 是本机环境参考，包含本机运行时提供的包。CI 从 `pyproject.toml` 的版本范围安装公开依赖，再用 `packaging/constraints-ci.txt` 固定编译器；实际安装版本写入发行包的 `dependencies.json`，构建清单另记录 Python、Nuitka 与 GitHub 提交 SHA。

`packaging/models.lock.json` 固定已使用的 Argos 1.9 英中双向模型 URL、大小和 SHA-256。`setup_translation.py` 不再从浮动模型索引选择最新版；下载在解压前校验，缓存命中检查 manifest 和关键文件。更换模型时应更新锁文件中的真实校验值，缓存键随之改变。

CI 显式指定模型目录：

```powershell
python scripts/setup_translation.py --model-dir .cache/offline-models --lock packaging/models.lock.json
```

## 可下载制品

首个 Release 包含：

- `CatiaManualViewer-0.5.0-windows-x64-portable.zip`：完整便携目录、QtWebEngine、离线模型、依赖许可证和宏安装说明。
- 同名 `.zip.sha256`：标准 SHA-256 校验文件。

使用者解压整个 ZIP 后运行 `CatiaManualViewer.exe`。包内不包含原版手册、CATIA 程序、个人配置、索引或密钥。构建器从其他工作目录执行实际 EXE 的运行时自检，再压缩；发布检查会进一步验证 ZIP 路径、版本、模型 manifest、默认相对路径及敏感文件。发布任务下载 Actions 制品后重新校验 ZIP 和 SHA-256。

编译报告和运行时 JSON 单独留在 Actions 的 `nuitka-diagnostics-*` 制品中。完整 CATIA 宏、真实手册、远程翻译 API 和独立电脑的交互验收仍使用本机验证流程，不在 GitHub runner 中伪造通过结果。

## 发布与重跑

发布顺序是：校验资产 → `gh release create --verify-tag --draft` → 上传 ZIP 与校验文件 → 转为公开。上传失败时草稿保留，重跑可以继续并替换草稿资产；已经公开的同标签 Release 不会被覆盖，脚本会报错，后续修订请使用新版本标签。

手动运行只构建制品，即使手动选择的是标签，也不会发布。标签构建不取消同一标签正在进行的发布，避免上传中断；再次运行时可从草稿恢复。

## 本地检查

```powershell
python scripts/release.py metadata --tag v0.5.0
python -m pytest --basetemp=.cache/pytest-tmp -q
python -m ruff check src tests scripts packaging
python scripts/release.py verify dist/<发行包名称>.zip --write-checksum
```

本轮已用 actionlint 1.7.12 检查两个工作流；验证了 offscreen 构造语料 GUI 测试，以及版本防错、模型 hash、目录越界、制品内容、校验文件、草稿恢复和公开版本保护。发布状态测试全部 mock GitHub CLI，不向 GitHub 写入数据。真正的远端安装、MSVC 编译和上传状态由维护者运行工作流后检查。
