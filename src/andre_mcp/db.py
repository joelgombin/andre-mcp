"""Gestion de la connexion DuckDB partagée."""

from pathlib import Path

import duckdb

# Connexion singleton
_connection: duckdb.DuckDBPyConnection | None = None
_current_path: Path | None = None


def get_connection(
    db_path: Path | None = None,
    load_spatial: bool = False,
) -> duckdb.DuckDBPyConnection:
    """Retourne une connexion DuckDB read-only (singleton).

    Args:
        db_path: Chemin vers le fichier .duckdb. Si None, utilise config.get_db_path().
        load_spatial: Si True, charge l'extension spatiale.

    Returns:
        Connexion DuckDB read-only.

    Raises:
        FileNotFoundError: Si le fichier DB n'existe pas.
    """
    global _connection, _current_path

    if db_path is None:
        from andre_mcp.config import get_db_path
        db_path = get_db_path()

    if not db_path.exists():
        raise FileNotFoundError(
            f"Base de données introuvable : {db_path}\n"
            "Lancez 'andre-mcp setup' pour télécharger les données."
        )

    # Réutilise la connexion si même path
    if _connection is not None and _current_path == db_path:
        if load_spatial:
            _connection.execute("LOAD spatial")
        return _connection

    # Ferme l'ancienne connexion si différent path
    if _connection is not None:
        try:
            _connection.close()
        except Exception:
            pass

    _connection = duckdb.connect(str(db_path), read_only=True)
    _current_path = db_path

    if load_spatial:
        _connection.execute("INSTALL spatial")
        _connection.execute("LOAD spatial")

    return _connection


def close_connection() -> None:
    """Ferme la connexion DuckDB singleton."""
    global _connection, _current_path

    if _connection is not None:
        try:
            _connection.close()
        except Exception:
            pass
        _connection = None
        _current_path = None
