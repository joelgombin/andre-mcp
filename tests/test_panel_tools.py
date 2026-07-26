#!/usr/bin/env python3
"""
Tests des actions « panel » ajoutées à l'outil query_elections (portage juin 2026).

Ces actions (vote_reserve, select_communes, cross_election_panel,
bureau_breakdown, compare_elections, query_all_circonscriptions) existaient déjà
dans l'agent principal (main.py) mais pas dans le serveur MCP standalone.

Stratégie : on intercepte `_run_script` pour capturer les flags CLI construits,
SANS exécuter le script ni toucher à la base DuckDB. On teste donc le mapping
action → flags, qui est précisément ce que ce portage modifie.

Usage:
    uv run python tests/test_panel_tools.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import andre_mcp.server as server_mod  # noqa: E402


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
        print("Résultats — actions panel query_elections")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


def _capture_args(arguments: dict):
    """Appelle query_elections via le serveur en interceptant _run_script.

    Retourne (script_name, args_list) capturés, ou lève si le script réel
    n'aurait pas été appelé (ex. validation qui retourne une erreur en amont).
    """
    captured = {}

    def fake_run_script(script_name, args):
        captured["script"] = script_name
        captured["args"] = list(args)
        return "FAKE_OK"

    original = server_mod._run_script
    server_mod._run_script = fake_run_script
    try:
        server = server_mod.create_server()

        async def _run():
            result = await server.call_tool("query_elections", arguments)
            first = result[0] if result else None
            if first is None:
                return ""
            if hasattr(first, "text"):
                return first.text
            if isinstance(first, list) and first and hasattr(first[0], "text"):
                return first[0].text
            return str(first)

        text = asyncio.run(_run())
    finally:
        server_mod._run_script = original

    return captured.get("script"), captured.get("args"), text


def test_vote_reserve(results: TestResult):
    print("\n[1] vote_reserve → flags CLI...")
    try:
        script, args, _ = _capture_args({
            "action": "vote_reserve",
            "commune_code": "13055",
            "famille": "Gauche",
        })
        assert script == "query_elections.py", f"script={script}"
        assert args == ["--vote-reserve", "--commune", "13055", "--famille", "Gauche", "--json"], \
            f"args={args}"
        results.add("vote_reserve mapping", True)
    except Exception as e:
        results.add("vote_reserve mapping", False, str(e))


def test_vote_reserve_requires_famille(results: TestResult):
    print("\n[2] vote_reserve sans famille → erreur, pas d'appel script...")
    try:
        script, args, text = _capture_args({
            "action": "vote_reserve",
            "commune_code": "13055",
        })
        assert script is None and args is None, "Le script ne devrait pas être appelé"
        assert "famille" in text.lower(), f"Message d'erreur attendu, obtenu : {text[:200]}"
        results.add("vote_reserve validation", True)
    except Exception as e:
        results.add("vote_reserve validation", False, str(e))


def test_select_communes(results: TestResult):
    print("\n[3] select_communes → flags CLI...")
    try:
        script, args, _ = _capture_args({
            "action": "select_communes",
            "departement_code": "13",
            "population_min": 10000,
            "commune_metric": "famille:Gauche",
            "condition": "above_average",
            "election_id": "2024_legi_t1",
            "sort_by": "metric",
            "limit": 20,
            "order": "desc",
        })
        assert script == "query_elections.py", f"script={script}"
        assert args[:2] == ["--select-communes", "--json"], f"prefix={args[:2]}"
        assert "--departement" in args and args[args.index("--departement") + 1] == "13"
        assert "--population-min" in args and args[args.index("--population-min") + 1] == "10000"
        assert "--commune-metric" in args and args[args.index("--commune-metric") + 1] == "famille:Gauche"
        assert "--condition" in args and args[args.index("--condition") + 1] == "above_average"
        assert "--sort-by" in args and args[args.index("--sort-by") + 1] == "metric"
        assert "--limit" in args and args[args.index("--limit") + 1] == "20"
        assert "--order" in args and args[args.index("--order") + 1] == "desc"
        results.add("select_communes mapping", True)
    except Exception as e:
        results.add("select_communes mapping", False, str(e))


def test_cross_election_panel(results: TestResult):
    print("\n[4] cross_election_panel → flags CLI...")
    try:
        script, args, _ = _capture_args({
            "action": "cross_election_panel",
            "elections": "2022_pres_t1,2024_legi_t1",
            "departement_code": "13",
            "famille": "Extrême droite",
        })
        assert script == "query_elections.py", f"script={script}"
        assert args[:2] == ["--cross-election", "--json"], f"prefix={args[:2]}"
        assert "--elections" in args and args[args.index("--elections") + 1] == "2022_pres_t1,2024_legi_t1"
        assert "--departement" in args and args[args.index("--departement") + 1] == "13"
        assert "--famille" in args and args[args.index("--famille") + 1] == "Extrême droite"
        results.add("cross_election_panel mapping", True)
    except Exception as e:
        results.add("cross_election_panel mapping", False, str(e))


def test_cross_election_panel_requires_subject(results: TestResult):
    print("\n[5] cross_election_panel sans famille/nuance → erreur...")
    try:
        script, args, text = _capture_args({
            "action": "cross_election_panel",
            "elections": "2022_pres_t1,2024_legi_t1",
            "departement_code": "13",
        })
        assert script is None, "Le script ne devrait pas être appelé"
        assert "famille" in text.lower() or "nuance" in text.lower(), \
            f"Message d'erreur attendu, obtenu : {text[:200]}"
        results.add("cross_election_panel validation", True)
    except Exception as e:
        results.add("cross_election_panel validation", False, str(e))


def test_bureau_breakdown(results: TestResult):
    print("\n[6] bureau_breakdown → flags CLI...")
    try:
        script, args, _ = _capture_args({
            "action": "bureau_breakdown",
            "commune_code": "13055",
            "election_id": "2024_legi_t1",
            "top_n": 8,
            "with_sociodem": True,
        })
        assert script == "query_elections.py", f"script={script}"
        assert args[:2] == ["--bureau-breakdown", "--json"], f"prefix={args[:2]}"
        assert "--commune" in args and args[args.index("--commune") + 1] == "13055"
        assert "--election" in args and args[args.index("--election") + 1] == "2024_legi_t1"
        assert "--top-n" in args and args[args.index("--top-n") + 1] == "8"
        assert "--with-sociodem" in args
        results.add("bureau_breakdown mapping", True)
    except Exception as e:
        results.add("bureau_breakdown mapping", False, str(e))


def test_compare_elections(results: TestResult):
    print("\n[7] compare_elections → flags CLI...")
    try:
        script, args, _ = _capture_args({
            "action": "compare_elections",
            "election_id": "2024_legi_t1",
            "compare_to_election": "2022_legi_t1",
            "commune_code": "13055",
            "candidate_name": "BOMPARD",
        })
        assert script == "query_elections.py", f"script={script}"
        assert "--election" in args and args[args.index("--election") + 1] == "2024_legi_t1"
        assert "--compare-to" in args and args[args.index("--compare-to") + 1] == "2022_legi_t1"
        assert "--commune" in args and args[args.index("--commune") + 1] == "13055"
        assert "--candidate" in args and args[args.index("--candidate") + 1] == "BOMPARD"
        results.add("compare_elections mapping", True)
    except Exception as e:
        results.add("compare_elections mapping", False, str(e))


def test_query_all_circonscriptions(results: TestResult):
    print("\n[8] query_all_circonscriptions → flags CLI...")
    try:
        script, args, _ = _capture_args({
            "action": "query_all_circonscriptions",
            "election_id": "2024_legi_t1",
            "departement_code": "13",
        })
        assert script == "query_elections.py", f"script={script}"
        assert "--all-circonscriptions" in args
        assert "--election" in args and args[args.index("--election") + 1] == "2024_legi_t1"
        assert "--departement" in args and args[args.index("--departement") + 1] == "13"
        results.add("query_all_circonscriptions mapping", True)
    except Exception as e:
        results.add("query_all_circonscriptions mapping", False, str(e))


def test_actions_listed_in_docstring(results: TestResult):
    """Les nouvelles actions doivent être documentées (le modèle lit le docstring)."""
    print("\n[9] Actions documentées dans le docstring...")
    try:
        async def _get_desc():
            server = server_mod.create_server()
            tools = await server.list_tools()
            for t in tools:
                if t.name == "query_elections":
                    return t.description or ""
            return ""

        desc = asyncio.run(_get_desc())
        for action in ["vote_reserve", "select_communes", "cross_election_panel",
                       "bureau_breakdown", "compare_elections", "query_all_circonscriptions"]:
            assert action in desc, f"Action '{action}' absente du docstring de query_elections"
        results.add("Actions documentées", True)
    except Exception as e:
        results.add("Actions documentées", False, str(e))


def main():
    print("=" * 60)
    print("Tests actions panel — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_vote_reserve(results)
    test_vote_reserve_requires_famille(results)
    test_select_communes(results)
    test_cross_election_panel(results)
    test_cross_election_panel_requires_subject(results)
    test_bureau_breakdown(results)
    test_compare_elections(results)
    test_query_all_circonscriptions(results)
    test_actions_listed_in_docstring(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
