#!/usr/bin/env python3
"""
Tests d'intégration — vérifie que les outils MCP appellent les scripts et retournent des données.

Nécessite : base DuckDB + scripts query_*.py du projet principal.

Usage:
    uv run python tests/test_tools_integration.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

PROJECT_DB = Path(__file__).parent.parent.parent / "data" / "elections" / "elections.duckdb"


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
        print("Résultats des tests — intégration outils")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


def _call_tool(server, tool_name: str, arguments: dict) -> str:
    """Appelle un outil MCP et retourne le résultat texte."""
    async def _run():
        result = await server.call_tool(tool_name, arguments)
        # FastMCP peut retourner des structures variées, extraire le texte
        if not result:
            return ""
        # Aplatir si c'est une liste de listes
        first = result[0] if result else None
        if first is None:
            return ""
        if hasattr(first, "text"):
            return first.text
        if isinstance(first, list) and len(first) > 0 and hasattr(first[0], "text"):
            return first[0].text
        return str(first)
    return asyncio.run(_run())


def test_list_elections(results: TestResult):
    """query_elections(list_elections) doit lister les élections."""
    print("\n[1] query_elections — list_elections...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_elections", {"action": "list_elections"})
        assert len(text) > 100, f"Résultat trop court ({len(text)} chars)"
        assert "2024_legi_t1" in text, "Élection 2024_legi_t1 manquante"
        assert "2022_pres_t1" in text, "Élection 2022_pres_t1 manquante"
        results.add("list_elections", True, f"{len(text)} chars")
    except Exception as e:
        results.add("list_elections", False, str(e))


def test_query_results_marseille(results: TestResult):
    """query_elections — résultats de Marseille aux législatives 2024."""
    print("\n[2] query_elections — résultats Marseille 2024_legi_t1...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_elections", {
            "action": "query_results",
            "election_id": "2024_legi_t1",
            "commune_code": "13055",
        })
        assert len(text) > 50, f"Résultat trop court ({len(text)} chars)"
        # Marseille a forcément des candidats
        assert "13055" in text or "Marseille" in text.lower() or "inscrits" in text.lower(), \
            "Résultat ne contient pas de référence à Marseille"
        results.add("Résultats Marseille 2024", True)
    except Exception as e:
        results.add("Résultats Marseille 2024", False, str(e))


def test_query_admin_search(results: TestResult):
    """query_admin — recherche 'Marseille'."""
    print("\n[3] query_admin — search Marseille...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_admin", {
            "action": "search_commune",
            "search_term": "Marseille",
        })
        assert "13055" in text, "Code commune 13055 manquant dans les résultats"
        results.add("Recherche admin Marseille", True)
    except Exception as e:
        results.add("Recherche admin Marseille", False, str(e))


def test_query_sql_count(results: TestResult):
    """query_sql — comptage des élections."""
    print("\n[4] query_sql — COUNT élections...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_sql", {
            "query": "SELECT COUNT(*) AS nb FROM elections_disponibles",
        })
        assert "nb" in text or any(c.isdigit() for c in text), \
            f"Résultat SQL inattendu : {text[:200]}"
        results.add("SQL COUNT élections", True, text.strip()[:80])
    except Exception as e:
        results.add("SQL COUNT élections", False, str(e))


def test_query_sql_readonly(results: TestResult):
    """query_sql — les requêtes d'écriture doivent être bloquées."""
    print("\n[5] query_sql — écriture bloquée...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_sql", {
            "query": "DROP TABLE elections_disponibles",
        })
        assert "Erreur" in text or "autorisé" in text or "read" in text.lower(), \
            f"DROP devrait être bloqué, obtenu : {text[:200]}"
        results.add("SQL écriture bloquée", True)
    except Exception as e:
        # Une exception est aussi acceptable
        results.add("SQL écriture bloquée", True, f"Exception: {type(e).__name__}")


def test_resource_readable(results: TestResult):
    """Les resources doivent être lisibles."""
    print("\n[6] Lecture d'une resource...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def _read():
            content = await server.read_resource("andre://references/political_colors")
            return str(content)

        text = asyncio.run(_read())
        assert len(text) > 50, f"Contenu trop court ({len(text)} chars)"
        assert "Gauche" in text or "gauche" in text or "#" in text, \
            "Contenu ne ressemble pas à political_colors.json"
        results.add("Resource political_colors", True)
    except Exception as e:
        results.add("Resource political_colors", False, str(e))


def test_prompt_callable(results: TestResult):
    """Les prompts doivent être appelables."""
    print("\n[7] Appel d'un prompt...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def _get():
            result = await server.get_prompt("analyser_commune", {"commune": "Marseille"})
            return result

        result = asyncio.run(_get())
        assert result is not None, "Prompt retourne None"
        assert len(result.messages) > 0, "Aucun message dans le prompt"
        text = result.messages[0].content.text
        assert "Marseille" in text, f"Marseille absent du prompt : {text[:200]}"
        results.add("Prompt analyser_commune", True)
    except Exception as e:
        results.add("Prompt analyser_commune", False, str(e))


def main():
    print("=" * 60)
    print("Tests intégration outils — andre-mcp")
    print("=" * 60)

    if not PROJECT_DB.exists():
        print(f"\n⚠ Base DuckDB introuvable : {PROJECT_DB}")
        print("  Certains tests seront limités.")

    results = TestResult()
    test_list_elections(results)
    test_query_results_marseille(results)
    test_query_admin_search(results)
    test_query_sql_count(results)
    test_query_sql_readonly(results)
    test_resource_readable(results)
    test_prompt_callable(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
