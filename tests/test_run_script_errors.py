#!/usr/bin/env python3
"""
Tests pour le formatage des erreurs de scripts dans le serveur MCP.

Régression : un script qui échoue avec un message multi-lignes (ex:
query_sociodem.py quand la table sociodem est absente) ne doit PAS être
réduit à sa seule dernière ligne. Le bug historique renvoyait
« Erreur :   3. uv run python setup_duckdb.py --rebuild » — la 3e ligne
d'un message « Exécuter : 1./2./3. » — en perdant la cause racine.

Tests :
1. Un message multi-lignes conserve sa cause racine (pas juste lines[-1])
2. Pas de double préfixe « Erreur : Erreur : »
3. Les reformulations argparse restent intactes (régression)
4. Un traceback Python garde la ligne d'exception finale

Usage:
    uv run python tests/test_run_script_errors.py
"""

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
        print("Résultats des tests — formatage erreurs _run_script")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


# Le stderr exact produit par query_sociodem.py quand la table est absente
SOCIODEM_STDERR = (
    "Erreur : données sociodémographiques non trouvées.\n"
    "Exécuter :\n"
    "  1. uv run python download_sociodem_data.py --all\n"
    "  2. uv run python build_bv_sociodem.py --all\n"
    "  3. uv run python setup_duckdb.py --rebuild"
)


def test_multiline_keeps_root_cause(results: TestResult):
    """Un message multi-lignes ne doit pas être réduit à sa dernière ligne."""
    print("\n[1] Message multi-lignes : conserve la cause racine...")
    try:
        from andre_mcp.server import _format_script_error

        msg = _format_script_error("query_sociodem.py", 1, SOCIODEM_STDERR)

        assert "données sociodémographiques non trouvées" in msg, (
            f"La cause racine a été perdue : {msg!r}"
        )
        assert ("build_bv_sociodem" in msg or "download_sociodem" in msg), (
            f"Les étapes de remédiation ont été perdues : {msg!r}"
        )
        # Le bug historique : message réduit à « Erreur :   3. ... »
        assert not msg.strip().startswith("Erreur :   3."), (
            f"Régression : message tronqué à la dernière ligne : {msg!r}"
        )
        results.add("Message multi-lignes conserve la cause racine", True)
    except AssertionError as e:
        results.add("Message multi-lignes conserve la cause racine", False, str(e))
    except Exception as e:
        results.add("Message multi-lignes conserve la cause racine", False, f"Erreur inattendue : {e}")


def test_no_double_erreur_prefix(results: TestResult):
    """Pas de double préfixe « Erreur : Erreur : » quand le stderr commence déjà par Erreur."""
    print("\n[2] Pas de double préfixe « Erreur : »...")
    try:
        from andre_mcp.server import _format_script_error

        msg = _format_script_error("query_sociodem.py", 1, SOCIODEM_STDERR)
        assert "Erreur : Erreur" not in msg, f"Double préfixe détecté : {msg!r}"
        results.add("Pas de double préfixe Erreur", True)
    except AssertionError as e:
        results.add("Pas de double préfixe Erreur", False, str(e))
    except Exception as e:
        results.add("Pas de double préfixe Erreur", False, f"Erreur inattendue : {e}")


def test_argparse_required_preserved(results: TestResult):
    """La reformulation des arguments manquants (argparse) reste fonctionnelle."""
    print("\n[3] Reformulation argparse (arguments requis)...")
    try:
        from andre_mcp.server import _format_script_error

        stderr = (
            "usage: query_elections.py [-h] ...\n"
            "query_elections.py: error: the following arguments are required: --election"
        )
        msg = _format_script_error("query_elections.py", 2, stderr)
        assert "paramètres manquants" in msg, f"Reformulation perdue : {msg!r}"
        assert "--election" in msg, f"Nom du paramètre manquant absent : {msg!r}"
        results.add("Reformulation argparse préservée", True)
    except AssertionError as e:
        results.add("Reformulation argparse préservée", False, str(e))
    except Exception as e:
        results.add("Reformulation argparse préservée", False, f"Erreur inattendue : {e}")


def test_traceback_keeps_exception_line(results: TestResult):
    """Un traceback Python garde la ligne d'exception finale (la plus utile)."""
    print("\n[4] Traceback : garde la ligne d'exception...")
    try:
        from andre_mcp.server import _format_script_error

        stderr = (
            "Traceback (most recent call last):\n"
            '  File "query_elections.py", line 10, in <module>\n'
            "    main()\n"
            '  File "query_elections.py", line 5, in main\n'
            "    con.execute(sql)\n"
            "duckdb.BinderException: Contents of view were altered: types don't match!"
        )
        msg = _format_script_error("query_elections.py", 1, stderr)
        assert "BinderException" in msg, f"Ligne d'exception perdue : {msg!r}"
        # Le bruit du traceback (chemins de fichiers) doit être filtré
        assert 'File "query_elections.py"' not in msg, f"Bruit traceback non filtré : {msg!r}"
        results.add("Traceback garde la ligne d'exception", True)
    except AssertionError as e:
        results.add("Traceback garde la ligne d'exception", False, str(e))
    except Exception as e:
        results.add("Traceback garde la ligne d'exception", False, f"Erreur inattendue : {e}")


def main():
    print("=" * 60)
    print("Tests formatage erreurs — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_multiline_keeps_root_cause(results)
    test_no_double_erreur_prefix(results)
    test_argparse_required_preserved(results)
    test_traceback_keeps_exception_line(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
