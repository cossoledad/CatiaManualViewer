"""CI release metadata, artifact verification and draft-first GitHub publication."""

import argparse
import hashlib
import json
import ntpath
import os
import re
import subprocess
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

PROJECT = Path(__file__).resolve().parents[1]


def metadata(project: Path = PROJECT, tag: str | None = None) -> dict:
    version = tomllib.loads((project / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("发布版本须为 X.Y.Z；当前发布流程仅支持正式版本")
    expected = "v" + version
    if tag is not None and tag != expected:
        raise ValueError(f"标签 {tag!r} 与 pyproject.toml 版本不一致，预期 {expected}")
    notes = project / "docs/releases" / (expected + ".md")
    if not notes.is_file():
        raise ValueError(f"缺少发布说明：{notes}")
    artifact = f"CatiaManualViewer-{version}-windows-x64-portable"
    return {
        "version": version,
        "tag": expected,
        "artifact": artifact,
        "directory": "dist/" + artifact,
        "archive": "dist/" + artifact + ".zip",
        "notes": notes.relative_to(project).as_posix(),
    }


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_archive(archive: Path, version: str):
    root = archive.stem
    with zipfile.ZipFile(archive) as package:
        normalized = [name.casefold() for name in package.namelist()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("制品包含重复或大小写冲突的 ZIP 路径")
        names = set(package.namelist())
        for name in names:
            path = PurePosixPath(name)
            if (
                "\\" in name
                or path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] != root
            ):
                raise ValueError(f"制品包含无效路径：{name}")
            if (
                path.name.casefold() in {"config.local.toml", "config.example.toml"}
                or path.suffix.lower()
                in {
                    ".sqlite",
                    ".sqlite3",
                    ".db",
                    ".log",
                    ".lnk",
                    ".catvbs",
                    ".catvba",
                    ".catpart",
                    ".catproduct",
                    ".catdrawing",
                }
                or any(
                    part.casefold() in {".git", ".venv", "catenv", "catsettings", "online"}
                    for part in path.parts[1:]
                )
            ):
                raise ValueError(f"制品混入个人数据或 CATIA 文件：{name}")
        required = (
            "CatiaManualViewer.exe",
            "config.default.toml",
            "build-info.json",
            "dependencies.json",
            "catia_manual/assets/reader_translation.js",
            "catia_manual/assets/reader_resources.js",
            "data/models/manifest.json",
        )
        for name in required:
            if root + "/" + name not in names:
                raise ValueError(f"制品缺少 {name}")
        info = json.loads(package.read(root + "/build-info.json"))
        if info["version"] != version or info["offline_models"] is not True:
            raise ValueError("制品版本或离线模型标识不正确")
        defaults = tomllib.loads(package.read(root + "/config.default.toml").decode("utf-8"))
        if defaults["translation"]["provider"] != "local":
            raise ValueError("含模型的发行包必须默认启用本机翻译")

        def contains_secret(value):
            return any(
                (
                    key.casefold() in {"api_key", "api_key_override", "token", "password"}
                    and bool(item)
                )
                or (isinstance(item, dict) and contains_secret(item))
                for key, item in value.items()
            )

        if contains_secret(defaults):
            raise ValueError("发行默认配置包含密钥")
        for section, keys in {
            "documentation": ("root", "database"),
            "catia": ("launcher", "environment_dir"),
            "translation": ("model_dir",),
        }.items():
            if any(ntpath.isabs(defaults[section][key]) for key in keys):
                raise ValueError("发行默认配置包含机器绝对路径")
        locked = json.loads((PROJECT / "packaging/models.lock.json").read_text("utf-8"))
        if json.loads(package.read(root + "/data/models/manifest.json")) != locked:
            raise ValueError("制品模型与 models.lock.json 不一致")
        for pair in locked:
            for name in ("model/model.bin", "sentencepiece.model", "metadata.json"):
                if root + f"/data/models/{pair}/" + name not in names:
                    raise ValueError(f"制品缺少模型文件 {pair}/{name}")
        if package.testzip() is not None:
            raise ValueError("制品 ZIP CRC 校验失败")


def checksum_path(archive):
    return archive.with_suffix(archive.suffix + ".sha256")


def write_checksum(archive):
    output = checksum_path(archive)
    output.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="ascii", newline="\n")
    return output


def verify_checksum(archive):
    expected = f"{sha256(archive)}  {archive.name}\n"
    if checksum_path(archive).read_text("ascii") != expected:
        raise ValueError("发布制品 SHA-256 校验失败")


def publish(repo: str, tag: str, directory: Path):
    info = metadata(tag=tag)
    archive = directory / (info["artifact"] + ".zip")
    verify_archive(archive, info["version"])
    verify_checksum(archive)
    command = ["gh", "release"]
    common = [tag, "--repo", repo]
    existing = subprocess.run(
        command + ["view", *common, "--json", "isDraft"], capture_output=True, text=True
    )
    if existing.returncode == 0:
        if not json.loads(existing.stdout)["isDraft"]:
            raise ValueError("该版本已公开发布；不会覆盖公开 Release，请使用新的版本标签")
    else:
        subprocess.run(
            command
            + [
                "create",
                *common,
                "--verify-tag",
                "--draft",
                "--title",
                "Catia Manual Viewer " + tag,
                "--notes-file",
                str(PROJECT / info["notes"]),
            ],
            check=True,
        )
    # A failure leaves a draft. A rerun may replace assets in that draft, never in a public release.
    subprocess.run(
        command + ["upload", *common, str(archive), str(checksum_path(archive)), "--clobber"],
        check=True,
    )
    subprocess.run(command + ["edit", *common, "--draft=false"], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    meta = commands.add_parser("metadata")
    meta.add_argument("--tag")
    verify = commands.add_parser("verify")
    verify.add_argument("archive", type=Path)
    verify.add_argument("--write-checksum", action="store_true")
    upload = commands.add_parser("publish")
    upload.add_argument("--repo", required=True)
    upload.add_argument("--tag", required=True)
    upload.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "metadata":
        info = metadata(tag=args.tag)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write("".join(f"{key}={value}\n" for key, value in info.items()))
    elif args.command == "verify":
        verify_archive(args.archive, metadata()["version"])
        if args.write_checksum:
            print(write_checksum(args.archive))
        else:
            verify_checksum(args.archive)
    else:
        publish(args.repo, args.tag, args.directory)


if __name__ == "__main__":
    main()
