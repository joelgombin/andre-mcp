#!/usr/bin/env python3
"""
Tests d'utilisabilité — reproduit les erreurs typiques que Claude fait
en utilisant les outils MCP, et vérifie qu'ils retournent des résultats utiles.

Ces tests simulent les appels réels que Claude ferait.

Usage:
    uv run python tests/test_tool_usability.py
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
        print("Résultats des tests — utilisabilité outils")
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
        if not result:
            return ""
        first = result[0] if result else None
        if first is None:
            return ""
        if hasattr(first, "text"):
            return first.text
        if isinstance(first, list) and len(first) > 0 and hasattr(first[0], "text"):
            return first[0].text
        return str(first)
    return asyncio.run(_run())


def test_generate_map_basic(results: TestResult):
    """generate_map avec les bons paramètres doit fonctionner."""
    print("\n[1] generate_map — carte famille par département...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "generate_map", {
            "election_id": "2024_legi_t1",
            "department": "13",
            "metric": "family",
        })
        assert "error" not in text.lower() or "erreur" not in text.lower(), \
            f"Erreur retournée : {text[:300]}"
        assert len(text) > 20, f"Résultat trop court : {text}"
        results.add("generate_map basique", True, text[:100])
    except Exception as e:
        results.add("generate_map basique", False, str(e)[:200])


def test_generate_map_commune(results: TestResult):
    """generate_map pour une commune spécifique (cas Marseille)."""
    print("\n[2] generate_map — carte Marseille...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "generate_map", {
            "election_id": "2020_muni_t2",
            "commune": "13055",
            "metric": "family",
        })
        assert "error: the following arguments" not in text.lower(), \
            f"Erreur argparse : {text[:300]}"
        results.add("generate_map Marseille", True, text[:100])
    except Exception as e:
        results.add("generate_map Marseille", False, str(e)[:200])


def test_generate_map_abstention(results: TestResult):
    """generate_map metric='abstention' — le cas qui échouait via le connecteur public."""
    print("\n[2b] generate_map — carte d'abstention Marseille...")
    try:
        import json
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "generate_map", {
            "election_id": "2024_legi_t1",
            "commune": "13055",
            "metric": "abstention",
        })
        assert "invalid choice" not in text.lower(), f"metric refusée : {text[:300]}"
        payload = json.loads(text)
        assert "error" not in payload, payload.get("error")
        assert "Abstention" in payload["title"], payload["title"]
        assert payload["spec"]["legend"]["type"] == "sequential"
        assert payload.get("map_image"), "pas d'image générée"
        results.add("generate_map abstention", True, payload["map_image"])
    except Exception as e:
        results.add("generate_map abstention", False, str(e)[:200])


def test_generate_map_unknown_metric(results: TestResult):
    """Une metric inconnue renvoie la liste des valeurs possibles, pas une trace argparse."""
    print("\n[2c] generate_map — metric inconnue...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "generate_map", {
            "election_id": "2024_legi_t1",
            "commune": "13055",
            "metric": "taux_abstention",
        })
        assert "abstention" in text and "participation" in text, text[:300]
        results.add("generate_map metric inconnue", True)
    except Exception as e:
        results.add("generate_map metric inconnue", False, str(e)[:200])


def test_query_elections_bad_action(results: TestResult):
    """query_elections avec une action invalide doit retourner un message clair."""
    print("\n[3] query_elections — action invalide...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_elections", {
            "action": "carte_abstention",
        })
        # Doit retourner un message d'erreur lisible, pas un crash
        assert len(text) > 0, "Résultat vide"
        # Ne doit pas contenir un traceback Python brut
        assert "Traceback" not in text, f"Traceback exposé : {text[:300]}"
        results.add("query_elections action invalide", True, text[:100])
    except Exception as e:
        results.add("query_elections action invalide", False, str(e)[:200])


def test_query_elections_with_commune_name(results: TestResult):
    """query_elections avec un nom de commune au lieu du code doit donner une erreur claire."""
    print("\n[4] query_elections — nom commune au lieu de code...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_elections", {
            "action": "query_results",
            "election_id": "2024_legi_t1",
            "commune_code": "Marseille",
        })
        # Doit soit retourner un résultat (si le script gère) soit un message clair
        assert "Traceback" not in text, f"Traceback exposé : {text[:300]}"
        results.add("query_elections nom commune", True, text[:100])
    except Exception as e:
        results.add("query_elections nom commune", False, str(e)[:200])


def test_query_sociodem_profile(results: TestResult):
    """query_sociodem profile pour Marseille doit fonctionner."""
    print("\n[5] query_sociodem — profil Marseille...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        text = _call_tool(server, "query_sociodem", {
            "action": "profile",
            "commune_code": "13055",
        })
        assert len(text) > 50, f"Résultat trop court : {text}"
        assert "Traceback" not in text, f"Traceback exposé : {text[:300]}"
        results.add("query_sociodem profil", True, f"{len(text)} chars")
    except Exception as e:
        results.add("query_sociodem profil", False, str(e)[:200])


def test_query_sql_with_common_query(results: TestResult):
    """query_sql avec une requête typique d'analyse doit fonctionner."""
    print("\n[6] query_sql — abstention par commune Marseille...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        # Note : dans les Parquet bruts, le code commune est sans préfixe département
        # (ex: '055' pour Marseille, pas '13055'). Le query_sql travaille sur les vues brutes.
        text = _call_tool(server, "query_sql", {
            "query": """
                SELECT id_election,
                       ROUND(100.0 * SUM(Abstentions) / NULLIF(SUM(Inscrits), 0), 1) as moy_abstention
                FROM general_results
                WHERE "Code du département" = '13'
                  AND "Code de la commune" = '055'
                GROUP BY id_election
                ORDER BY id_election DESC
                LIMIT 5
            """,
        })
        assert "moy_abstention" in text or "abstention" in text.lower(), \
            f"Résultat inattendu : {text[:300]}"
        results.add("query_sql abstention", True, text[:100])
    except Exception as e:
        results.add("query_sql abstention", False, str(e)[:200])


def test_tool_descriptions_quality(results: TestResult):
    """Les descriptions d'outils doivent être claires et contenir des exemples."""
    print("\n[7] Qualité des descriptions d'outils...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def check():
            return await server.list_tools()

        tools = asyncio.run(check())
        issues = []
        for tool in tools:
            desc = tool.description or ""
            # Vérifier la longueur minimale
            if len(desc) < 30:
                issues.append(f"{tool.name}: description trop courte ({len(desc)} chars)")
            # Vérifier que les paramètres importants sont mentionnés
            if tool.name == "generate_map" and "election_id" not in desc.lower() and "election" not in desc.lower():
                issues.append(f"generate_map: 'election' absent de la description")
            if tool.name == "query_elections" and "action" not in desc.lower():
                issues.append(f"query_elections: 'action' absent de la description")

        if issues:
            results.add("Qualité descriptions", False, "; ".join(issues))
        else:
            results.add("Qualité descriptions", True)
    except Exception as e:
        results.add("Qualité descriptions", False, str(e)[:200])


def main():
    print("=" * 60)
    print("Tests utilisabilité — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_generate_map_basic(results)
    test_generate_map_commune(results)
    test_generate_map_abstention(results)
    test_generate_map_unknown_metric(results)
    test_query_elections_bad_action(results)
    test_query_elections_with_commune_name(results)
    test_query_sociodem_profile(results)
    test_query_sql_with_common_query(results)
    test_tool_descriptions_quality(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
