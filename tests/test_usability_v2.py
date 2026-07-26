#!/usr/bin/env python3
"""
Tests d'utilisabilité v2 — resource sql_schema, meilleurs messages d'erreur,
prompt carte_electorale amélioré, raccourci abstention.

Usage:
    uv run python tests/test_usability_v2.py
"""

import asyncio
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
        print("Résultats des tests — utilisabilité v2")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


def _call_tool(server, tool_name: str, arguments: dict) -> str:
    async def _run():
        result = await server.call_tool(tool_name, arguments)
        if not result:
            return ""
        first = result[0]
        if first is None:
            return ""
        if hasattr(first, "text"):
            return first.text
        if isinstance(first, list) and len(first) > 0 and hasattr(first[0], "text"):
            return first[0].text
        return str(first)
    return asyncio.run(_run())


# ==========================================
# 1. Resource sql_schema
# ==========================================

def test_sql_schema_resource_exists(results: TestResult):
    """La resource sql_schema doit exister et lister les tables."""
    print("\n[1] Resource sql_schema existe...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def check():
            resources = await server.list_resources()
            return resources

        resources = asyncio.run(check())
        uris = [str(r.uri) for r in resources]
        assert any("sql_schema" in u for u in uris), \
            f"sql_schema absent des resources : {uris}"
        results.add("Resource sql_schema existe", True)
    except Exception as e:
        results.add("Resource sql_schema existe", False, str(e)[:200])


def test_sql_schema_content(results: TestResult):
    """La resource sql_schema doit contenir les noms de colonnes clés."""
    print("\n[2] Contenu sql_schema...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def read():
            return await server.read_resource("andre://references/sql_schema")

        content = str(asyncio.run(read()))
        # Doit contenir les tables principales
        assert "general_results" in content, "general_results absent"
        assert "candidats_results" in content, "candidats_results absent"
        assert "elections_disponibles" in content, "elections_disponibles absent"
        # Doit mentionner le piège des noms de colonnes
        assert "Code de la commune" in content or "code_commune" in content, \
            "Pas de mention des noms de colonnes"
        # Doit mentionner le format sans préfixe département
        assert "055" in content or "sans préfixe" in content.lower() or "sans le code" in content.lower(), \
            "Pas de mention du format code commune"
        results.add("Contenu sql_schema", True, f"{len(content)} chars")
    except Exception as e:
        results.add("Contenu sql_schema", False, str(e)[:200])


# ==========================================
# 2. Meilleurs messages d'erreur
# ==========================================

def test_error_message_bad_election(results: TestResult):
    """Une élection invalide doit retourner un message lisible, pas un traceback."""
    print("\n[3] Erreur élection invalide...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_elections", {
            "action": "query_results",
            "election_id": "2099_fake_t1",
            "commune_code": "13055",
        })
        assert "Traceback" not in text, f"Traceback exposé : {text[:200]}"
        assert len(text) > 0, "Résultat vide"
        results.add("Erreur élection invalide", True, text[:100])
    except Exception as e:
        # Une RuntimeError avec un message lisible est acceptable
        msg = str(e)
        assert "Traceback" not in msg, f"Traceback dans l'exception : {msg[:200]}"
        results.add("Erreur élection invalide", True, f"Exception: {msg[:80]}")


def test_error_message_generate_map_missing_dept(results: TestResult):
    """generate_map sans department ni commune doit donner un message clair."""
    print("\n[4] Erreur generate_map sans territoire...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "generate_map", {
            "election_id": "2024_legi_t1",
            "metric": "family",
        })
        # Doit fonctionner (carte nationale) ou donner un message clair
        assert "Traceback" not in text, f"Traceback exposé : {text[:200]}"
        results.add("generate_map sans territoire", True, text[:100])
    except Exception as e:
        msg = str(e)
        assert "Traceback" not in msg
        results.add("generate_map sans territoire", True, f"Exception gérée: {msg[:80]}")


# ==========================================
# 3. Prompt carte_electorale amélioré
# ==========================================

def test_prompt_carte_has_workflow(results: TestResult):
    """Le prompt carte_electorale doit guider vers query_elections PUIS generate_map."""
    print("\n[5] Prompt carte_electorale guide le workflow...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def get():
            return await server.get_prompt("carte_electorale",
                                           {"election": "2024_legi_t1", "departement": "13"})

        result = asyncio.run(get())
        text = result.messages[0].content.text
        # Doit mentionner query_elections ET generate_map
        assert "query_elections" in text or "query_admin" in text, \
            f"Ne mentionne pas les outils de requête : {text[:200]}"
        assert "generate_map" in text, \
            f"Ne mentionne pas generate_map : {text[:200]}"
        results.add("Prompt carte workflow", True)
    except Exception as e:
        results.add("Prompt carte workflow", False, str(e)[:200])


# ==========================================
# 4. Raccourci abstention
# ==========================================

def test_query_elections_abstention_action(results: TestResult):
    """query_elections doit supporter une action 'rank_abstention' ou similaire."""
    print("\n[6] query_elections — rank_bureaux abstention Marseille...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        # L'action rank_bureaux avec metric=abstention devrait fonctionner
        text = _call_tool(server, "query_elections", {
            "action": "rank_bureaux",
            "election_id": "2020_muni_t2",
            "commune_code": "13055",
            "rank_metric": "abstention",
            "limit": 5,
        })
        assert len(text) > 50, f"Résultat trop court : {text}"
        assert "Traceback" not in text, f"Traceback : {text[:200]}"
        # Doit contenir des données de bureau de vote
        results.add("rank_bureaux abstention", True, f"{len(text)} chars")
    except Exception as e:
        results.add("rank_bureaux abstention", False, str(e)[:200])


def test_query_elections_participation_ranking(results: TestResult):
    """rank_bureaux avec metric=participation doit retourner les taux."""
    print("\n[7] query_elections — rank participation Marseille...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_elections", {
            "action": "rank_bureaux",
            "election_id": "2024_legi_t1",
            "commune_code": "13055",
            "rank_metric": "participation",
            "limit": 5,
        })
        # Doit contenir des données de participation
        has_data = any(w in text.lower() for w in ["participation", "inscrits", "votants", "%"])
        assert has_data, f"Pas de données participation : {text[:300]}"
        assert len(text) > 50, f"Résultat trop court : {text}"
        results.add("Rank participation", True, text[:100])
    except Exception as e:
        results.add("Rank participation", False, str(e)[:200])


def main():
    print("=" * 60)
    print("Tests utilisabilité v2 — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_sql_schema_resource_exists(results)
    test_sql_schema_content(results)
    test_error_message_bad_election(results)
    test_error_message_generate_map_missing_dept(results)
    test_prompt_carte_has_workflow(results)
    test_query_elections_abstention_action(results)
    test_query_elections_participation_ranking(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
