import json
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path


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


def load_settings(path: Path | None = None) -> Settings:
    """Resolve relative settings against the config file, never the caller's cwd."""
    project = Path(__file__).resolve().parents[2]
    if path is None:
        candidates = [
            Path.cwd() / "config.local.toml",
            project / "config.local.toml",
            Path.cwd() / "config.example.toml",
            project / "config.example.toml",
        ]
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            # Wheels also work outside the source checkout; --config overrides these defaults.
            installation = Path(r"C:\UserApp\3D\DS\Dassault Systemes")
            return Settings(
                root=installation / "B33doc" / "English",
                database=Path.cwd().resolve() / "data" / "manuals.sqlite3",
                launcher=installation / "B33" / "win_b64" / "code" / "bin" / "CATSTART.exe",
                environment="CATIA_P3.V5-6R2023.B33",
                environment_dir=installation / "CATEnv",
            )
    path = path.resolve()
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    doc, catia = data["documentation"], data["catia"]

    def absolute(value: str) -> Path:
        result = Path(value).expanduser()
        return result.resolve() if result.is_absolute() else (path.parent / result).resolve()

    translation = dict(data.get("translation", {"provider": "glossary"}))
    translation["model_dir"] = str(absolute(translation.get("model_dir", "data/models")))
    return Settings(
        root=absolute(doc["root"]),
        database=absolute(doc["database"]),
        launcher=absolute(catia["launcher"]),
        environment=catia["environment"],
        environment_dir=absolute(catia["environment_dir"]),
        homepage=doc.get("homepage", "CATIA_P3_homepage.htm"),
        translation=translation,
        config_path=path,
    )


def save_translation(settings: Settings, values: dict) -> Settings:
    source = settings.config_path
    if source is None:
        source = Path.cwd() / "config.example.toml"
    destination = (
        source.with_name("config.local.toml") if source.name == "config.example.toml" else source
    )
    if source.is_file():
        original = source.read_text(encoding="utf-8")
    else:

        def literal(value):
            return json.dumps(str(value), ensure_ascii=False)

        original = (
            "[documentation]\nroot = "
            + literal(settings.root)
            + "\nhomepage = "
            + literal(settings.homepage)
            + "\ndatabase = "
            + literal(settings.database)
            + "\n\n[catia]\nlauncher = "
            + literal(settings.launcher)
            + "\nenvironment = "
            + literal(settings.environment)
            + "\nenvironment_dir = "
            + literal(settings.environment_dir)
            + "\n"
        )
    config = dict(settings.translation)
    config.update(values)
    section = (
        "[translation]\n"
        + "\n".join(
            key
            + " = "
            + (
                str(value).lower()
                if isinstance(value, bool)
                else str(value)
                if isinstance(value, int)
                else json.dumps(str(value), ensure_ascii=False)
            )
            for key, value in config.items()
        )
        + "\n"
    )
    pattern = r"(?ms)^\[translation\]\s*\n.*?(?=^\[|\Z)"
    updated = (
        re.sub(pattern, lambda _: section + "\n", original)
        if re.search(pattern, original)
        else (original.rstrip() + "\n\n" + section)
    )
    temporary = destination.with_suffix(".toml.tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(destination)
    return replace(settings, translation=config, config_path=destination)
