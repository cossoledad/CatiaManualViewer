"""Install official Argos model data locally; no translation runtime downloads are performed."""

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

import httpx

from catia_manual.config import load_settings

DEFAULT_LOCK = Path(__file__).resolve().parents[1] / "packaging/models.lock.json"
REQUIRED_FILES = ("model/model.bin", "metadata.json", "sentencepiece.model")


def read_lock(path: Path) -> dict:
    packages = json.loads(path.read_text("utf-8"))
    if set(packages) != {"en_zh", "zh_en"}:
        raise ValueError("模型锁文件必须包含 en_zh 和 zh_en")
    for package in packages.values():
        if (
            not package["url"].startswith("https://")
            or not re.fullmatch(r"[0-9a-f]{64}", package["sha256"])
            or not isinstance(package["archive_bytes"], int)
            or package["archive_bytes"] <= 0
        ):
            raise ValueError("模型锁文件中的 HTTPS 地址、SHA-256 或大小无效")
    return packages


def install(model_dir: Path, lock: Path = DEFAULT_LOCK):
    packages = read_lock(lock)
    model_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = model_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.is_file() else {}
    with httpx.Client(follow_redirects=True, timeout=180) as client:
        for pair, selected in packages.items():
            if manifest.get(pair) == selected and all(
                (model_dir / pair / name).is_file() for name in REQUIRED_FILES
            ):
                print(f"{pair}: already installed", flush=True)
                continue
            url = selected["url"]
            archive = model_dir / f"{pair}.argosmodel"
            digest = hashlib.sha256()
            print(f"{pair}: downloading {url}", flush=True)
            with client.stream("GET", url) as download, archive.open("wb") as output:
                download.raise_for_status()
                for chunk in download.iter_bytes():
                    output.write(chunk)
                    digest.update(chunk)
            if (
                digest.hexdigest() != selected["sha256"]
                or archive.stat().st_size != selected["archive_bytes"]
            ):
                archive.unlink()
                raise ValueError(f"{pair}: 模型下载包校验失败，未解压或更新 manifest")
            destination = model_dir / pair
            destination.mkdir(exist_ok=True)
            with zipfile.ZipFile(archive) as package:
                for member in package.infolist():
                    parts = Path(member.filename.replace("\\", "/")).parts
                    if len(parts) < 2 or member.is_dir():
                        continue
                    relative = Path(*parts[1:])
                    output_path = (destination / relative).resolve()
                    if not output_path.is_relative_to(destination.resolve()):
                        raise ValueError("模型压缩包路径无效")
                    if relative.parts[0] not in {
                        "model",
                        "sentencepiece.model",
                        "metadata.json",
                        "README.md",
                        "LICENSE",
                        "LICENSE.txt",
                    }:
                        continue
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(member) as incoming, output_path.open("wb") as output:
                        shutil.copyfileobj(incoming, output)
            for required in REQUIRED_FILES:
                if not (destination / required).is_file():
                    raise ValueError(f"模型缺少 {required}，请检查包格式")
            manifest[pair] = selected
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            archive.unlink()
            print(f"{pair}: installed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--model-dir", type=Path, help="explicit destination, independent of local config"
    )
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args()
    if args.model_dir:
        directory = args.model_dir
    else:
        settings = load_settings(args.config)
        directory = Path(settings.translation.get("model_dir", settings.database.parent / "models"))
    install(directory, args.lock)
