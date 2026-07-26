#!/usr/bin/env python3
"""
Tests pour le module config — résolution du répertoire de données.

TDD RED : ces tests doivent échouer tant que config.py n'est pas implémenté.

Tests :
1. get_data_dir() retourne un Path
2. ANDRE_DATA_DIR override fonctionne
3. Le répertoire par défaut utilise platformdirs
4. data_status() retourne un dict avec les clés attendues

Usage:
    uv run python tests/test_config.py
"""

import os
import sys
from pathlib import Path

# Ajouter le src au path pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestResult:
    """Suivi simple des résultats de test."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.results = []

    def add(self, name: str, passed: bool, message: str = "", skipped: bool = False):
        if skipped:
            self.skipped += 1
            status = "SKIP"
        elif passed:
            self.passed += 1
            status = "PASS"
        else:
            self.failed += 1
            status = "FAIL"
        self.results.append((name, status, message))

    def print_summary(self):
        print("\n" + "=" * 60)
        print("Résultats des tests — config")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


def test_get_data_dir_returns_path(results: TestResult):
    """get_data_dir() doit retourner un objet Path."""
    print("\n[1] get_data_dir() retourne un Path...")
    try:
        from andre_mcp.config import get_data_dir

        data_dir = get_data_dir()
        assert isinstance(data_dir, Path), f"Attendu Path, obtenu {type(data_dir)}"
        results.add("get_data_dir() retourne un Path", True)
    except Exception as e:
        results.add("get_data_dir() retourne un Path", False, str(e))


def test_env_override(results: TestResult):
    """ANDRE_DATA_DIR doit surcharger le répertoire par défaut."""
    print("\n[2] ANDRE_DATA_DIR override...")
    try:
        from andre_mcp.config import get_data_dir

        custom_path = "/tmp/andre-mcp-test-data"
        old_val = os.environ.get("ANDRE_DATA_DIR")
        try:
            os.environ["ANDRE_DATA_DIR"] = custom_path
            data_dir = get_data_dir()
            assert data_dir == Path(custom_path), f"Attendu {custom_path}, obtenu {data_dir}"
            results.add("ANDRE_DATA_DIR override", True)
        finally:
            if old_val is None:
                os.environ.pop("ANDRE_DATA_DIR", None)
            else:
                os.environ["ANDRE_DATA_DIR"] = old_val
    except Exception as e:
        results.add("ANDRE_DATA_DIR override", False, str(e))


def test_default_uses_platformdirs(results: TestResult):
    """Le répertoire par défaut doit utiliser platformdirs."""
    print("\n[3] Répertoire par défaut utilise platformdirs...")
    try:
        from andre_mcp.config import get_data_dir

        old_val = os.environ.pop("ANDRE_DATA_DIR", None)
        try:
            data_dir = get_data_dir()
            # Doit contenir "andre-mcp" ou "andre_mcp" dans le chemin
            assert "andre" in str(data_dir).lower(), (
                f"Le chemin par défaut devrait contenir 'andre': {data_dir}"
            )
            results.add("Répertoire par défaut utilise platformdirs", True)
        finally:
            if old_val is not None:
                os.environ["ANDRE_DATA_DIR"] = old_val
    except Exception as e:
        results.add("Répertoire par défaut utilise platformdirs", False, str(e))


def test_data_status(results: TestResult):
    """data_status() doit retourner un dict avec les clés essentielles."""
    print("\n[4] data_status() retourne un dict structuré...")
    try:
        from andre_mcp.config import data_status

        status = data_status()
        assert isinstance(status, dict), f"Attendu dict, obtenu {type(status)}"
        required_keys = {"data_dir", "duckdb_exists", "datasets"}
        missing = required_keys - set(status.keys())
        assert not missing, f"Clés manquantes: {missing}"
        assert isinstance(status["datasets"], dict), "datasets doit être un dict"
        results.add("data_status() retourne un dict structuré", True)
    except Exception as e:
        results.add("data_status() retourne un dict structuré", False, str(e))


def test_get_db_path(results: TestResult):
    """get_db_path() doit retourner le chemin vers elections.duckdb."""
    print("\n[5] get_db_path() retourne le chemin DuckDB...")
    try:
        from andre_mcp.config import get_db_path

        db_path = get_db_path()
        assert isinstance(db_path, Path), f"Attendu Path, obtenu {type(db_path)}"
        assert db_path.name == "elections.duckdb", f"Attendu elections.duckdb, obtenu {db_path.name}"
        results.add("get_db_path() retourne le chemin DuckDB", True)
    except Exception as e:
        results.add("get_db_path() retourne le chemin DuckDB", False, str(e))


def main():
    print("=" * 60)
    print("Tests config — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_get_data_dir_returns_path(results)
    test_env_override(results)
    test_default_uses_platformdirs(results)
    test_data_status(results)
    test_get_db_path(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
