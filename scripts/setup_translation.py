"""Install official Argos model data locally; no translation runtime downloads are performed."""

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import httpx

from catia_manual.config import load_settings

INDEX = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"


def install(model_dir: Path):
    model_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = model_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.is_file() else {}
    with httpx.Client(follow_redirects=True, timeout=180) as client:
        response = client.get(INDEX)
        response.raise_for_status()
        packages = response.json()
        for source, target in (("en", "zh"), ("zh", "en")):
            pair = f"{source}_{target}"
            required_files = ("model/model.bin", "metadata.json", "sentencepiece.model")
            if pair in manifest and all(
                (model_dir / pair / name).is_file() for name in required_files
            ):
                print(f"{pair}: already installed", flush=True)
                continue
            candidates = [
                p for p in packages if p["from_code"] == source and p["to_code"] == target
            ]
            selected = max(
                candidates, key=lambda p: tuple(int(x) for x in p["package_version"].split("."))
            )
            url = next(link for link in selected["links"] if link.startswith("https://"))
            archive = model_dir / f"{pair}.argosmodel"
            digest = hashlib.sha256()
            print(f"{pair}: downloading {url}", flush=True)
            with client.stream("GET", url) as download, archive.open("wb") as output:
                download.raise_for_status()
                for chunk in download.iter_bytes():
                    output.write(chunk)
                    digest.update(chunk)
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
            for required in required_files:
                if not (destination / required).is_file():
                    raise ValueError(f"模型缺少 {required}，请检查包格式")
            manifest[pair] = {
                "url": url,
                "version": selected["package_version"],
                "sha256": digest.hexdigest(),
                "archive_bytes": archive.stat().st_size,
            }
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            archive.unlink()
            print(f"{pair}: installed", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    settings = load_settings(args.config)
    directory = Path(settings.translation.get("model_dir", settings.database.parent / "models"))
    install(directory)
