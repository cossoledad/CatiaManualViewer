import hashlib
import re
import sqlite3
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit

from catia_manual.core.providers import provider_profile

INSTALLATION = Path(r"C:\UserApp\3D\DS\Dassault Systemes")
APPEARANCE = {
    "font_size": 10,
    "reader_zoom": 100,
    "show_tools": True,
    "remember_window": True,
    "window_width": 1480,
    "window_height": 940,
}


@dataclass(frozen=True)
class Settings:
    root: Path
    database: Path
    launcher: Path
    environment: str
    environment_dir: Path
    homepage: str = "CATIA_P3_homepage.htm"
    translation: dict = field(default_factory=lambda: {"provider": "glossary"})
    config_path: Path | None = None
    appearance: dict = field(default_factory=lambda: dict(APPEARANCE))


def database_for_root(database: Path, root: Path) -> Path:
    """A relocated library gets its own index; never rebind or overwrite another library."""
    if not database.is_file():
        return database
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()
        if row and Path(row[0]).resolve() == root.resolve():
            return database
    except sqlite3.DatabaseError:
        pass
    digest = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:12]
    stem = re.sub(r"-[a-f0-9]{12}$", "", database.stem)
    return database.with_name(f"{stem}-{digest}{database.suffix}")


def load_settings(path: Path | None = None) -> Settings:
    project = Path(__file__).resolve().parents[2]
    if path is None:
        candidates = [
            Path.cwd() / "config.local.toml",
            project / "config.local.toml",
            Path.cwd() / "config.example.toml",
            project / "config.example.toml",
        ]
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
    base = path.resolve().parent if path else Path.cwd().resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path else {}
    doc, catia = data.get("documentation", {}), data.get("catia", {})

    def absolute(value) -> Path:
        result = Path(value).expanduser()
        return result.resolve() if result.is_absolute() else (base / result).resolve()

    root = absolute(doc.get("root", INSTALLATION / "B33doc/English"))
    translation = dict(data.get("translation", {"provider": "local"}))
    translation.setdefault("provider", "local")
    translation.setdefault("automatic", True)
    translation.setdefault("selection_delay_ms", 350)
    translation.setdefault("max_selection_chars", 3000)
    translation.setdefault("target_language", "zh-CN")
    translation["model_dir"] = str(absolute(translation.get("model_dir", "data/models")))
    translation.update(provider_profile(translation, translation["provider"]))
    return Settings(
        root=root,
        database=database_for_root(absolute(doc.get("database", "data/manuals.sqlite3")), root),
        launcher=absolute(
            catia.get("launcher", INSTALLATION / "B33/win_b64/code/bin/CATSTART.exe")
        ),
        environment=catia.get("environment", "CATIA_P3.V5-6R2023.B33"),
        environment_dir=absolute(catia.get("environment_dir", INSTALLATION / "CATEnv")),
        homepage=doc.get("homepage", "CATIA_P3_homepage.htm"),
        translation=translation,
        config_path=path.resolve() if path else None,
        appearance={**APPEARANCE, **data.get("appearance", {})},
    )


def save_settings(settings: Settings, changes: dict) -> Settings:
    source = settings.config_path or Path.cwd() / "config.example.toml"
    destination = (
        source.with_name("config.local.toml") if source.name == "config.example.toml" else source
    )
    document = (
        tomlkit.parse(source.read_text(encoding="utf-8-sig"))
        if source.is_file()
        else tomlkit.document()
    )
    base_sections = {
        "documentation": {
            "root": settings.root,
            "database": settings.database,
            "homepage": settings.homepage,
        },
        "catia": {
            "launcher": settings.launcher,
            "environment": settings.environment,
            "environment_dir": settings.environment_dir,
        },
        "translation": settings.translation,
        "appearance": settings.appearance,
    }
    for section, defaults in base_sections.items():
        if section not in document:
            document[section] = tomlkit.table()
        values = {**dict(document[section]), **defaults, **changes.get(section, {})}
        for key, value in values.items():
            if key in {"root", "database", "launcher", "environment_dir", "model_dir"}:
                candidate = Path(value).expanduser()
                if not candidate.is_absolute():
                    candidate = destination.parent / candidate
                candidate = candidate.resolve()
                value = (
                    candidate.relative_to(destination.parent).as_posix()
                    if (candidate.is_relative_to(destination.parent))
                    else str(candidate)
                )
            document[section][key] = value
    temporary = destination.with_suffix(".toml.tmp")
    temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
    temporary.replace(destination)
    return load_settings(destination)


def save_translation(settings: Settings, values: dict) -> Settings:
    return save_settings(settings, {"translation": {**settings.translation, **values}})
