# CI 与发布

当前版本 **0.6.0**，标签 **v0.6.0**。使用者只下载 Windows x64 Nuitka 便携版。

## 构建流程

分支 push、Pull Request 或手动运行工作流生成可下载制品；推送 `vX.Y.Z` 标签会在构建成功后发布 Release。CI 只构建、打包和检查制品，不运行 pytest、Ruff 或 GUI。

构建使用 Windows 2022、Python 3.12、MSVC 与 `packaging/constraints-ci.txt` 固定的编译依赖；离线模型按 `packaging/models.lock.json` 校验。输出 ZIP、SHA-256 和单独的编译报告。ZIP 中包含 EXE、QtWebEngine、离线模型、许可证及使用说明，不含个人配置、索引或手册。

项目版本 `pyproject.toml`、程序 `src/catia_manual/__init__.py` 的 `__version__`、标签和 `docs/releases/vX.Y.Z.md` 必须一致；`scripts/release.py metadata` 在安装构建依赖前校验，EXE 与 build-info 使用相同版本。

## 发布

```powershell
python scripts/release.py metadata --tag v0.6.0
# 提交并推送已验证的代码后，在相同提交创建标签：
git tag v0.6.0
git push origin v0.6.0
```

发布任务重新校验 ZIP 和校验文件，创建或恢复草稿，上传成功后转为公开。上传失败可重跑草稿；公开 Release 不自动覆盖。手动运行工作流只构建，不发布。

如果标签因版本不一致而失败，重跑旧提交仍会失败。先修正版本、提交并推送，再核实失败标签尚无公开 Release，使用带明确旧 SHA 的 force-with-lease 将标签移动至修正提交并推送，触发重新构建。不要对已公开版本使用此方式。

```powershell
git tag -f v0.6.0 <修正提交>
git push --force-with-lease=refs/tags/v0.6.0:<旧标签SHA> origin refs/tags/v0.6.0
```

公开资产只来自 CI 的干净源码构建。个人迁移包不能上传到 Release。`verify` 检查路径、版本、模型 manifest、凭据、机器路径与禁止文件；SHA-256 在上传前再次校验。

```powershell
python scripts/release.py verify dist/<发行包名称>.zip --write-checksum
```
