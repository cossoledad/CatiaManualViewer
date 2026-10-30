"""Developer-only Nuitka standalone build; never copy local config, index or credentials."""

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, help="include a verified offline model directory")
    parser.add_argument("--compiler", choices=("mingw64", "msvc"), default="mingw64")
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 2))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--skip-runtime-check", action="store_true", help="package without launching the built EXE"
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows portable releases must be built on Windows")
    if args.compiler == "mingw64" and sys.version_info[:2] != (3, 12):
        parser.error("Use Python 3.12 for MinGW64, or select --compiler msvc")
    version = tomllib.loads((PROJECT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    release = (args.output_dir or PROJECT / "dist" / f"CatiaManualViewer-{version}").resolve()
    if release.exists():
        parser.error(f"Release directory already exists; choose a new --output-dir: {release}")
    models = args.models.resolve() if args.models else None
    if models:
        for language in ("en_zh", "zh_en"):
            for name in ("model/model.bin", "sentencepiece.model", "metadata.json"):
                if not (models / language / name).is_file():
                    parser.error(f"Missing offline model file: {models / language / name}")
    work = PROJECT / ".cache" / "nuitka"
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["NUITKA_CACHE_DIR"] = str(work / "downloads")
    env["TEMP"] = env["TMP"] = str(PROJECT / ".cache" / "tmp")
    Path(env["TEMP"]).mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "nuitka",
        "--mode=standalone",
        "--enable-plugin=pyside6",
        "--include-qt-plugins=sensible",
        "--windows-console-mode=disable",
        "--windows-icon-from-ico=" + str(PROJECT / "src/catia_manual/assets/app.ico"),
        "--output-filename=CatiaManualViewer.exe",
        "--product-name=Catia Manual Viewer",
        "--product-version=" + version,
        "--file-version=" + version,
        "--file-description=CATIA V5 local documentation viewer",
        "--include-package=catia_manual",
        "--include-package-data=catia_manual",
        "--user-package-configuration-file=" + str(PROJECT / "packaging/nuitka-package.config.yml"),
        "--nofollow-import-to=catia_manual.cli",
        "--nofollow-import-to=pytest",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=torch",
        "--assume-yes-for-downloads",
        "--jobs=" + str(args.jobs),
        "--output-dir=" + str(work),
        "--report=" + str(work / "compilation-report.xml"),
        "--mingw64" if args.compiler == "mingw64" else "--msvc=latest",
        str(PROJECT / "packaging/portable_entry.py"),
    ]
    subprocess.run(command, cwd=PROJECT, env=env, check=True)
    # copytree refuses an existing destination; a build never overwrites a user's portable data.
    shutil.copytree(work / "portable_entry.dist", release)
    defaults = (PROJECT / "packaging/config.default.toml").read_text("utf-8")
    if models:
        shutil.copytree(models, release / "data/models")
        defaults = defaults.replace("provider = 'glossary'", "provider = 'local'")
    (release / "config.default.toml").write_text(defaults, encoding="utf-8")
    shutil.copy2(PROJECT / "packaging/使用说明.txt", release)
    manifest = {
        "product": "Catia Manual Viewer",
        "version": version,
        "format": "nuitka-standalone",
        "python": sys.version.split()[0],
        "nuitka": importlib.metadata.version("Nuitka"),
        "source_revision": os.environ.get("GITHUB_SHA"),
        "offline_models": models is not None,
        "entry": "CatiaManualViewer.exe",
    }
    (release / "build-info.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    distributions = (
        "beautifulsoup4",
        "soupsieve",
        "typing_extensions",
        "lxml",
        "charset-normalizer",
        "tomlkit",
        "PySide6",
        "PySide6_Addons",
        "PySide6_Essentials",
        "PySide6_WebEngine",
        "PySide6_Pdf",
        "shiboken6",
        "pywin32",
        "httpx",
        "httpcore",
        "h11",
        "anyio",
        "idna",
        "certifi",
        "sniffio",
        "ctranslate2",
        "sentencepiece",
        "numpy",
        "setuptools",
        "PyYAML",
    )
    inventory = []
    for name in distributions:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        inventory.append({"name": name, "version": distribution.version})
        for file in distribution.files or ():
            if any(token in file.name.lower() for token in ("license", "copying", "notice")):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    target = release / "licenses" / name / file
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    (release / "dependencies.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    if not args.skip_runtime_check:
        # Local builds can check the binary from a foreign directory before archiving.
        report = work / "portable-runtime.json"
        subprocess.run(
            [str(release / "CatiaManualViewer.exe"), "--diagnostics", str(report)],
            cwd=work,
            check=True,
            timeout=180,
        )
        verification = json.loads(report.read_text("utf-8"))
        if not verification["compiled"] or Path(verification["application_directory"]) != release:
            raise RuntimeError("Portable runtime resolved the wrong application directory")
    archive = shutil.make_archive(
        str(release), "zip", root_dir=release.parent, base_dir=release.name
    )
    print(archive)


if __name__ == "__main__":
    main()
