import tomllib
from dataclasses import dataclass, field
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

    return Settings(
        root=absolute(doc["root"]),
        database=absolute(doc["database"]),
        launcher=absolute(catia["launcher"]),
        environment=catia["environment"],
        environment_dir=absolute(catia["environment_dir"]),
        homepage=doc.get("homepage", "CATIA_P3_homepage.htm"),
        translation=data.get("translation", {"provider": "glossary"}),
    )
