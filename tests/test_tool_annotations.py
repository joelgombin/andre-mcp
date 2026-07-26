#!/usr/bin/env python3
"""
Tests des annotations d'outils MCP (title + readOnlyHint / destructiveHint).

Prérequis de soumission à l'annuaire de connecteurs Anthropic :
« All tools must include a `title` and the applicable `readOnlyHint` or
`destructiveHint` ».
https://claude.com/docs/connectors/building/submission

Tests :
1. Chaque outil expose un `title` non vide
2. Chaque outil expose des annotations avec readOnlyHint explicite
3. Les outils de consultation sont bien déclarés en lecture seule
4. Aucun outil n'est déclaré destructif (le serveur est en lecture seule)

Usage:
    uv run python tests/test_tool_annotations.py
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
        print("Résultats des tests")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(
            f"\nTotal : {self.passed} réussis, {self.failed} échoués, "
            f"{self.skipped} ignorés"
        )
        return self.failed == 0


# Outils de pure consultation : doivent être readOnlyHint=True
READ_ONLY_TOOLS = {
    "query_elections",
    "query_admin",
    "query_elus",
    "query_sociodem",
    "query_dlp",
    "query_sql",
    "simulate_fusions",
}


def get_tools():
    from andre_mcp.server import create_server

    mcp = create_server()
    return asyncio.run(mcp.list_tools())


def test_all_tools_have_title(results: TestResult, tools):
    """Chaque outil doit exposer un titre lisible."""
    print("\n[1] Titres des outils...")

    missing = [t.name for t in tools if not getattr(t, "title", None)]
    results.add(
        "Tous les outils ont un title",
        not missing,
        f"Outils sans title : {missing}" if missing else f"{len(tools)} outils",
    )


def test_all_tools_have_annotations(results: TestResult, tools):
    """Chaque outil doit exposer readOnlyHint explicitement."""
    print("\n[2] Annotations readOnlyHint...")

    missing = [
        t.name
        for t in tools
        if t.annotations is None or t.annotations.readOnlyHint is None
    ]
    results.add(
        "Tous les outils ont readOnlyHint",
        not missing,
        f"Outils sans readOnlyHint : {missing}" if missing else f"{len(tools)} outils",
    )


def test_read_only_tools_flagged(results: TestResult, tools):
    """Les outils de consultation sont déclarés en lecture seule."""
    print("\n[3] Outils de consultation en lecture seule...")

    by_name = {t.name: t for t in tools}
    wrong = [
        name
        for name in READ_ONLY_TOOLS
        if name in by_name
        and (
            by_name[name].annotations is None
            or by_name[name].annotations.readOnlyHint is not True
        )
    ]
    results.add(
        "Outils de consultation readOnlyHint=True",
        not wrong,
        f"Mal annotés : {wrong}" if wrong else f"{len(READ_ONLY_TOOLS)} outils vérifiés",
    )


def test_no_destructive_tool(results: TestResult, tools):
    """Aucun outil ne doit être destructif : André est en lecture seule."""
    print("\n[4] Absence d'outil destructif...")

    destructive = [
        t.name
        for t in tools
        if t.annotations is not None and t.annotations.destructiveHint is True
    ]
    results.add(
        "Aucun outil destructif",
        not destructive,
        f"Outils destructifs : {destructive}" if destructive else "",
    )


def main():
    print("=" * 60)
    print("Annotations des outils MCP")
    print("=" * 60)

    results = TestResult()

    try:
        tools = get_tools()
    except Exception as e:  # pragma: no cover - échec d'import
        results.add("Instanciation du serveur", False, f"Erreur : {e}")
        results.print_summary()
        return 1

    test_all_tools_have_title(results, tools)
    test_all_tools_have_annotations(results, tools)
    test_read_only_tools_flagged(results, tools)
    test_no_destructive_tool(results, tools)

    return 0 if results.print_summary() else 1


if __name__ == "__main__":
    sys.exit(main())
