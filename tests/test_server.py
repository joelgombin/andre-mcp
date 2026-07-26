#!/usr/bin/env python3
"""
Tests pour le serveur MCP — FastMCP avec outils, resources et prompts.

TDD RED : ces tests doivent échouer tant que server.py n'est pas implémenté.

Tests :
1. Le serveur FastMCP s'instancie sans erreur
2. Le serveur expose des outils (tools)
3. Le serveur expose des resources
4. Le serveur expose des prompts
5. Le point d'entrée CLI fonctionne

Usage:
    uv run python tests/test_server.py
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
        print("Résultats des tests — server")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


def test_server_instantiates(results: TestResult):
    """Le serveur FastMCP doit s'instancier sans erreur."""
    print("\n[1] Instanciation du serveur...")
    try:
        from andre_mcp.server import create_server

        server = create_server()
        assert server is not None, "Le serveur ne devrait pas être None"
        results.add("Instanciation du serveur", True)
    except Exception as e:
        results.add("Instanciation du serveur", False, str(e))


def test_server_has_tools(results: TestResult):
    """Le serveur doit exposer au moins un outil."""
    print("\n[2] Le serveur expose des outils...")
    try:
        from andre_mcp.server import create_server

        server = create_server()

        # FastMCP expose les outils via list_tools()
        async def check():
            tools = await server.list_tools()
            return tools

        tools = asyncio.run(check())
        assert len(tools) > 0, f"Aucun outil trouvé (attendu >= 1)"
        # Vérifie qu'au moins query_elections est présent
        tool_names = [t.name for t in tools]
        assert "query_elections" in tool_names, (
            f"query_elections manquant. Outils: {tool_names}"
        )
        results.add("Le serveur expose des outils", True, f"{len(tools)} outils")
    except Exception as e:
        results.add("Le serveur expose des outils", False, str(e))


def test_server_has_resources(results: TestResult):
    """Le serveur doit exposer des resources (références)."""
    print("\n[3] Le serveur expose des resources...")
    try:
        from andre_mcp.server import create_server

        server = create_server()

        async def check():
            resources = await server.list_resources()
            return resources

        resources = asyncio.run(check())
        assert len(resources) > 0, f"Aucune resource trouvée (attendu >= 1)"
        results.add("Le serveur expose des resources", True, f"{len(resources)} resources")
    except Exception as e:
        results.add("Le serveur expose des resources", False, str(e))


def test_server_has_prompts(results: TestResult):
    """Le serveur doit exposer des prompts pré-construits."""
    print("\n[4] Le serveur expose des prompts...")
    try:
        from andre_mcp.server import create_server

        server = create_server()

        async def check():
            prompts = await server.list_prompts()
            return prompts

        prompts = asyncio.run(check())
        assert len(prompts) > 0, f"Aucun prompt trouvé (attendu >= 1)"
        results.add("Le serveur expose des prompts", True, f"{len(prompts)} prompts")
    except Exception as e:
        results.add("Le serveur expose des prompts", False, str(e))


def test_version_accessible(results: TestResult):
    """La version du package doit être accessible."""
    print("\n[5] Version accessible...")
    try:
        from andre_mcp import __version__

        assert __version__ is not None, "Version ne devrait pas être None"
        assert isinstance(__version__, str), f"Version devrait être str, obtenu {type(__version__)}"
        results.add("Version accessible", True, f"v{__version__}")
    except Exception as e:
        results.add("Version accessible", False, str(e))


def main():
    print("=" * 60)
    print("Tests server — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_server_instantiates(results)
    test_server_has_tools(results)
    test_server_has_resources(results)
    test_server_has_prompts(results)
    test_version_accessible(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
