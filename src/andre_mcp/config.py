"""Configuration — résolution du répertoire de données andre-mcp."""

import os
from pathlib import Path

from platformdirs import user_data_dir

# Nom de l'application pour platformdirs
APP_NAME = "andre-mcp"

# Jeux de données connus et leurs sous-répertoires
DATASETS = {
    "resultats": "resultats",
    "administratif": "administratif",
    "ferd": "ferd",
    "contours": "contours",
    "geometries": "geometries",
    "candidatures": "candidatures",
    "bref": "bref",
    "rne": "rne",
    "sociodem": "sociodem",
}


def get_data_dir() -> Path:
    """Retourne le répertoire de données.

    Ordre de priorité :
    1. Variable d'environnement ANDRE_DATA_DIR
    2. Répertoire platformdirs (XDG / Application Support)
    """
    env_dir = os.environ.get("ANDRE_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(user_data_dir(APP_NAME))


def get_db_path() -> Path:
    """Retourne le chemin vers la base DuckDB."""
    return get_data_dir() / "elections.duckdb"


def data_status() -> dict:
    """Retourne l'état des données installées.

    Returns:
        dict avec les clés :
        - data_dir: Path du répertoire de données
        - duckdb_exists: bool
        - datasets: dict {nom: {"path": Path, "exists": bool, "size_mb": float}}
    """
    data_dir = get_data_dir()
    db_path = get_db_path()

    datasets_status = {}
    for name, subdir in DATASETS.items():
        ds_path = data_dir / subdir
        exists = ds_path.exists() and any(ds_path.iterdir()) if ds_path.exists() else False
        size_mb = 0.0
        if exists:
            size_mb = sum(f.stat().st_size for f in ds_path.rglob("*") if f.is_file()) / (1024 * 1024)
        datasets_status[name] = {
            "path": ds_path,
            "exists": exists,
            "size_mb": round(size_mb, 1),
        }

    return {
        "data_dir": data_dir,
        "duckdb_exists": db_path.exists(),
        "datasets": datasets_status,
    }
