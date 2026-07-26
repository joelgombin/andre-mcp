#!/usr/bin/env python3
"""
Tests pour le module db — connexion DuckDB partagée.

TDD RED : ces tests doivent échouer tant que db.py n'est pas implémenté.

Tests :
1. get_connection() retourne un objet DuckDB
2. La connexion est read-only
3. load_spatial=True charge l'extension spatiale
4. close_connection() ferme proprement
5. get_connection() avec un path invalide lève une erreur claire

Usage:
    uv run python tests/test_db.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


class TestResult:
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
        print("Résultats des tests — db")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


# Chemin vers la vraie DB du projet principal (pour les tests d'intégration)
PROJECT_DB = Path(__file__).parent.parent.parent / "data" / "elections" / "elections.duckdb"


def test_get_connection_returns_duckdb(results: TestResult):
    """get_connection() doit retourner un objet connexion DuckDB."""
    print("\n[1] get_connection() retourne un objet DuckDB...")
    if not PROJECT_DB.exists():
        results.add("get_connection() retourne un objet DuckDB", False, skipped=True,
                     message=f"DB introuvable: {PROJECT_DB}")
        return
    try:
        from andre_mcp.db import get_connection

        con = get_connection(db_path=PROJECT_DB)
        assert con is not None, "La connexion ne devrait pas être None"
        # Vérifie que c'est bien un objet DuckDB
        result = con.execute("SELECT 1 AS test").fetchone()
        assert result == (1,), f"Requête test échouée: {result}"
        results.add("get_connection() retourne un objet DuckDB", True)
    except Exception as e:
        results.add("get_connection() retourne un objet DuckDB", False, str(e))


def test_connection_is_read_only(results: TestResult):
    """La connexion doit être read-only (pas de CREATE TABLE)."""
    print("\n[2] La connexion est read-only...")
    if not PROJECT_DB.exists():
        results.add("La connexion est read-only", False, skipped=True,
                     message=f"DB introuvable: {PROJECT_DB}")
        return
    try:
        from andre_mcp.db import get_connection

        con = get_connection(db_path=PROJECT_DB)
        try:
            con.execute("CREATE TABLE test_readonly (id INTEGER)")
            results.add("La connexion est read-only", False,
                        "CREATE TABLE a réussi — la connexion n'est pas read-only !")
        except Exception:
            # C'est le comportement attendu : l'écriture doit échouer
            results.add("La connexion est read-only", True)
    except Exception as e:
        results.add("La connexion est read-only", False, str(e))


def test_invalid_path_raises_error(results: TestResult):
    """get_connection() avec un path invalide doit lever une erreur claire."""
    print("\n[3] Path invalide lève une erreur...")
    try:
        from andre_mcp.db import get_connection

        try:
            con = get_connection(db_path=Path("/tmp/nonexistent_andre_test.duckdb"))
            results.add("Path invalide lève une erreur", False,
                        "Aucune erreur levée pour un path invalide")
        except FileNotFoundError:
            results.add("Path invalide lève une erreur", True)
        except Exception as e:
            # Accepte d'autres types d'erreur tant qu'il y en a une
            results.add("Path invalide lève une erreur", True,
                        f"Erreur levée (type: {type(e).__name__})")
    except Exception as e:
        results.add("Path invalide lève une erreur", False, str(e))


def test_close_connection(results: TestResult):
    """close_connection() doit fermer proprement sans erreur."""
    print("\n[4] close_connection() ferme proprement...")
    if not PROJECT_DB.exists():
        results.add("close_connection() ferme proprement", False, skipped=True,
                     message=f"DB introuvable: {PROJECT_DB}")
        return
    try:
        from andre_mcp.db import close_connection, get_connection

        con = get_connection(db_path=PROJECT_DB)
        close_connection()
        # Après fermeture, une nouvelle connexion devrait fonctionner
        con2 = get_connection(db_path=PROJECT_DB)
        result = con2.execute("SELECT 1").fetchone()
        assert result == (1,), "Nouvelle connexion après fermeture échouée"
        close_connection()
        results.add("close_connection() ferme proprement", True)
    except Exception as e:
        results.add("close_connection() ferme proprement", False, str(e))


def test_list_elections_query(results: TestResult):
    """La DB doit pouvoir lister les élections via la vue elections_disponibles."""
    print("\n[5] Requête elections_disponibles fonctionne...")
    if not PROJECT_DB.exists():
        results.add("Requête elections_disponibles", False, skipped=True,
                     message=f"DB introuvable: {PROJECT_DB}")
        return
    try:
        from andre_mcp.db import close_connection, get_connection

        con = get_connection(db_path=PROJECT_DB)
        result = con.execute("SELECT COUNT(*) FROM elections_disponibles").fetchone()
        count = result[0]
        assert count > 0, f"Aucune élection trouvée (count={count})"
        assert count >= 20, f"Trop peu d'élections ({count}), attendu >= 20"
        results.add("Requête elections_disponibles", True, f"{count} élections trouvées")
        close_connection()
    except Exception as e:
        results.add("Requête elections_disponibles", False, str(e))


def main():
    print("=" * 60)
    print("Tests db — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_get_connection_returns_duckdb(results)
    test_connection_is_read_only(results)
    test_invalid_path_raises_error(results)
    test_close_connection(results)
    test_list_elections_query(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
