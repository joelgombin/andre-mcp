#!/usr/bin/env python3
"""
Tests CI — fonctionnent SANS les données complètes du projet.

Utilise la fixture minimale (tests/fixtures/) quand disponible,
sinon teste uniquement la structure du serveur (sans appels de données).

Conçu pour GitHub Actions. Pas de dépendance aux scripts query_*.py.

Usage :
    uv run python tests/test_ci.py
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_DB = FIXTURE_DIR / "elections.duckdb"


def _ensure_fixture_db():
    """Reconstruit le DuckDB fixture si absent ou périmé.

    Le .duckdb versionné contient des chemins absolus de la machine d'origine.
    On le reconstruit systématiquement à partir des Parquet (qui sont portables).
    """
    parquet_dir = FIXTURE_DIR / "resultats"
    if not parquet_dir.exists():
        return False

    # Toujours reconstruire — les Parquet sont la source de vérité
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from andre_mcp.setup_cli import build_duckdb
    build_duckdb(data_dir=FIXTURE_DIR, db_path=FIXTURE_DB)
    return FIXTURE_DB.exists()


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
        print("Résultats des tests CI — andre-mcp")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


# ==========================================
# TESTS UNITAIRES (pas besoin de données)
# ==========================================

def test_package_imports(results: TestResult):
    """Le package andre_mcp s'importe sans erreur."""
    print("\n[1] Import du package...")
    try:
        from andre_mcp import __version__
        from andre_mcp.config import get_data_dir, get_db_path, data_status
        from andre_mcp.db import get_connection, close_connection
        from andre_mcp.server import create_server, run, run_http
        from andre_mcp.setup_cli import DATASETS, download_file, check_dataset_status

        assert __version__ is not None
        results.add("Import package", True, f"v{__version__}")
    except Exception as e:
        results.add("Import package", False, str(e)[:200])


def test_config_env_override(results: TestResult):
    """ANDRE_DATA_DIR override fonctionne."""
    print("\n[2] Config ANDRE_DATA_DIR...")
    try:
        from andre_mcp.config import get_data_dir

        old = os.environ.get("ANDRE_DATA_DIR")
        os.environ["ANDRE_DATA_DIR"] = "/tmp/andre-test-ci"
        try:
            assert str(get_data_dir()) == "/tmp/andre-test-ci"
            results.add("Config env override", True)
        finally:
            if old is None:
                os.environ.pop("ANDRE_DATA_DIR", None)
            else:
                os.environ["ANDRE_DATA_DIR"] = old
    except Exception as e:
        results.add("Config env override", False, str(e)[:200])


def test_server_structure(results: TestResult):
    """Le serveur expose 11 outils, 9 resources, 5 prompts."""
    print("\n[3] Structure du serveur...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def check():
            tools = await server.list_tools()
            resources = await server.list_resources()
            prompts = await server.list_prompts()
            return tools, resources, prompts

        tools, resources, prompts = asyncio.run(check())

        tool_names = sorted([t.name for t in tools])
        expected_tools = [
            "create_data_table", "export_data", "generate_map",
            "query_admin", "query_dlp", "query_elections", "query_elus",
            "query_sociodem", "query_sql", "simulate_fusions", "visualize",
        ]
        assert tool_names == expected_tools, f"Outils: {tool_names}"
        assert len(resources) >= 9, f"Resources: {len(resources)}"
        assert len(prompts) >= 5, f"Prompts: {len(prompts)}"

        results.add("Structure serveur", True,
                     f"{len(tools)} outils, {len(resources)} resources, {len(prompts)} prompts")
    except Exception as e:
        results.add("Structure serveur", False, str(e)[:200])


def test_tool_descriptions_not_empty(results: TestResult):
    """Chaque outil a une description non vide."""
    print("\n[4] Descriptions d'outils...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def check():
            return await server.list_tools()

        tools = asyncio.run(check())
        empty = [t.name for t in tools if not t.description or len(t.description) < 20]
        assert not empty, f"Descriptions vides/courtes : {empty}"
        results.add("Descriptions outils", True)
    except Exception as e:
        results.add("Descriptions outils", False, str(e)[:200])


def test_datasets_catalog(results: TestResult):
    """Le catalogue DATASETS contient les jeux essentiels."""
    print("\n[5] Catalogue DATASETS...")
    try:
        from andre_mcp.setup_cli import DATASETS

        required = {"general_results", "candidats_results", "communes", "nuances"}
        missing = required - set(DATASETS.keys())
        assert not missing, f"Manquants: {missing}"

        for name, ds in DATASETS.items():
            assert ds.get("url", "").startswith("http"), f"{name}: URL invalide"
            assert ds.get("output"), f"{name}: pas d'output"
            assert ds.get("category"), f"{name}: pas de catégorie"

        results.add("Catalogue DATASETS", True, f"{len(DATASETS)} datasets")
    except Exception as e:
        results.add("Catalogue DATASETS", False, str(e)[:200])


def test_sql_schema_resource(results: TestResult):
    """La resource sql_schema contient les informations clés."""
    print("\n[6] Resource sql_schema...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def read():
            return await server.read_resource("andre://references/sql_schema")

        content = str(asyncio.run(read()))
        assert "general_results" in content
        assert "Code de la commune" in content
        assert "055" in content or "sans préfixe" in content.lower()
        results.add("Resource sql_schema", True, f"{len(content)} chars")
    except Exception as e:
        results.add("Resource sql_schema", False, str(e)[:200])


def test_prompts_callable(results: TestResult):
    """Les prompts retournent des messages avec les bons paramètres."""
    print("\n[7] Prompts appelables...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def check():
            r1 = await server.get_prompt("analyser_commune", {"commune": "Marseille"})
            r2 = await server.get_prompt("carte_electorale",
                                         {"election": "2024_legi_t1", "departement": "13"})
            return r1, r2

        r1, r2 = asyncio.run(check())
        assert "Marseille" in r1.messages[0].content.text
        assert "generate_map" in r2.messages[0].content.text
        assert "query_elections" in r2.messages[0].content.text
        results.add("Prompts appelables", True)
    except Exception as e:
        results.add("Prompts appelables", False, str(e)[:200])


def test_stdio_protocol(results: TestResult):
    """Le serveur répond au protocole MCP via stdio."""
    print("\n[8] Protocole stdio...")
    try:
        src_dir = str(Path(__file__).parent.parent / "src")
        messages = [
            json.dumps({
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ci-test", "version": "1.0"},
                },
                "id": 1,
            }),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2}),
        ]

        proc = subprocess.run(
            [sys.executable, "-c", f"""
import sys
sys.path.insert(0, '{src_dir}')
from andre_mcp.server import create_server
server = create_server()
server.run(transport='stdio')
"""],
            input="\n".join(messages) + "\n",
            capture_output=True, text=True, timeout=30,
        )

        responses = []
        for line in proc.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    if "id" in parsed:
                        responses.append(parsed)
                except json.JSONDecodeError:
                    pass

        # Vérifier initialize
        init_resp = next((r for r in responses if r.get("id") == 1), None)
        assert init_resp and "result" in init_resp, f"Pas de réponse initialize"
        assert "serverInfo" in init_resp["result"]

        # Vérifier tools/list
        tools_resp = next((r for r in responses if r.get("id") == 2), None)
        assert tools_resp and "result" in tools_resp, f"Pas de réponse tools/list"
        tool_names = [t["name"] for t in tools_resp["result"]["tools"]]
        assert "query_elections" in tool_names

        results.add("Protocole stdio", True,
                     f"{len(tool_names)} outils via stdio")
    except subprocess.TimeoutExpired:
        results.add("Protocole stdio", False, "Timeout 30s")
    except Exception as e:
        results.add("Protocole stdio", False, str(e)[:200])


# ==========================================
# TESTS D'INTÉGRATION (fixture requise)
# ==========================================

def test_fixture_db_queries(results: TestResult):
    """Requêtes sur la fixture DuckDB."""
    print("\n[9] Requêtes fixture DuckDB...")
    if not FIXTURE_DB.exists():
        results.add("Fixture DuckDB", False, skipped=True, message="Fixture absente")
        return
    try:
        from andre_mcp.db import close_connection, get_connection

        con = get_connection(db_path=FIXTURE_DB)

        # Élections disponibles
        n = con.execute("SELECT COUNT(*) FROM elections_disponibles").fetchone()[0]
        assert n >= 2, f"Attendu >= 2 élections, trouvé {n}"

        # Résultats Marseille
        n2 = con.execute("""
            SELECT COUNT(*) FROM general_results
            WHERE "Code du département" = '13' AND "Code de la commune" = '055'
        """).fetchone()[0]
        assert n2 > 0, f"Aucun résultat Marseille dans la fixture"

        # Familles politiques
        families = con.execute(
            "SELECT DISTINCT famille_politique FROM resultats_par_famille"
        ).fetchall()
        assert len(families) >= 2, f"Trop peu de familles : {families}"

        close_connection()
        results.add("Fixture DuckDB", True, f"{n} élections, {n2} BDV Marseille")
    except Exception as e:
        results.add("Fixture DuckDB", False, str(e)[:200])


def test_fixture_query_sql_tool(results: TestResult):
    """L'outil query_sql fonctionne sur la fixture."""
    print("\n[10] query_sql sur fixture...")
    if not FIXTURE_DB.exists():
        results.add("query_sql fixture", False, skipped=True, message="Fixture absente")
        return
    try:
        from andre_mcp.server import create_server

        # Pointer le serveur vers la fixture
        old_env = os.environ.get("ANDRE_DATA_DIR")
        os.environ["ANDRE_DATA_DIR"] = str(FIXTURE_DIR)
        try:
            server = create_server()

            async def call():
                result = await server.call_tool("query_sql", {
                    "query": "SELECT COUNT(*) as n FROM elections_disponibles",
                })
                first = result[0]
                if hasattr(first, "text"):
                    return first.text
                if isinstance(first, list) and first and hasattr(first[0], "text"):
                    return first[0].text
                return str(first)

            text = asyncio.run(call())
            assert "n" in text, f"Résultat inattendu : {text[:200]}"
        finally:
            if old_env is None:
                os.environ.pop("ANDRE_DATA_DIR", None)
            else:
                os.environ["ANDRE_DATA_DIR"] = old_env

        results.add("query_sql fixture", True, text.strip()[:80])
    except Exception as e:
        results.add("query_sql fixture", False, str(e)[:200])


def test_visualize_tool(results: TestResult):
    """L'outil visualize retourne un spec Plotly valide."""
    print("\n[11] visualize (pas de données requises)...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def call():
            result = await server.call_tool("visualize", {
                "chart_type": "bar",
                "data": [
                    {"parti": "Gauche", "voix": 140000},
                    {"parti": "Droite", "voix": 62000},
                    {"parti": "RN", "voix": 115000},
                ],
                "x": "parti",
                "y": "voix",
                "title": "Test CI",
            })
            # Extraire le texte depuis la structure de retour
            first = result[0]
            if hasattr(first, "text"):
                return first.text
            if isinstance(first, list) and first and hasattr(first[0], "text"):
                return first[0].text
            return str(first)

        text = asyncio.run(call())
        spec = json.loads(text)
        assert "data" in spec, f"Pas de 'data' dans le spec"
        assert "layout" in spec, f"Pas de 'layout' dans le spec"
        assert spec["layout"]["title"] == "Test CI"
        results.add("visualize", True)
    except Exception as e:
        results.add("visualize", False, str(e)[:200])


def test_create_data_table_tool(results: TestResult):
    """L'outil create_data_table formate un tableau."""
    print("\n[12] create_data_table (pas de données requises)...")
    try:
        from andre_mcp.server import create_server
        server = create_server()

        async def call():
            result = await server.call_tool("create_data_table", {
                "title": "Test CI",
                "columns": [
                    {"key": "commune", "label": "Commune"},
                    {"key": "score", "label": "Score %"},
                ],
                "rows": [
                    {"commune": "Marseille", "score": "42.1"},
                    {"commune": "Aix", "score": "38.5"},
                ],
            })
            first = result[0]
            return first.text if hasattr(first, "text") else str(first)

        text = asyncio.run(call())
        assert "Test CI" in text
        assert "Marseille" in text
        assert "Aix" in text
        results.add("create_data_table", True)
    except Exception as e:
        results.add("create_data_table", False, str(e)[:200])


def test_mcp_pin_below_v2(results: TestResult):
    """Régression : mcp 2.x supprime `mcp.server.fastmcp` (renommé MCPServer).

    Les Dockerfiles installent les dépendances depuis les pyproject.toml
    (résolution fraîche, hors uv.lock) : sans borne haute, le build embarque
    mcp 2.x et le serveur MCP crashe au démarrage avec
    ModuleNotFoundError: No module named 'mcp.server.fastmcp'.
    """
    import re

    root = Path(__file__).parent.parent.parent
    try:
        for rel in ("pyproject.toml", "andre-mcp/pyproject.toml"):
            content = (root / rel).read_text()
            specs = re.findall(r'"mcp((?:[<>=!~,][^"]*)?)"', content)
            assert specs, f"{rel} : aucune contrainte explicite sur `mcp`"
            for spec in specs:
                assert "<2" in spec, f"{rel} : `mcp{spec}` doit borner en dessous de 2.0"

        # L'API v1 utilisée par server.py doit rester importable
        from mcp.server.fastmcp import FastMCP  # noqa: F401

        results.add("Pin mcp<2", True)
    except AssertionError as e:
        results.add("Pin mcp<2", False, str(e))
    except Exception as e:
        results.add("Pin mcp<2", False, f"{type(e).__name__}: {str(e)[:200]}")


def test_dockerfiles_install_from_lock(results: TestResult):
    """Régression : les images doivent installer depuis uv.lock, pas résoudre à neuf.

    `uv pip install -r pyproject.toml` fait une résolution fraîche à chaque
    build : l'image devient une fonction de la date du build et non du commit.
    C'est ce qui a fait basculer la prod sur mcp 2.x (77 des 127 paquets
    divergeaient du lock, dont 8 sauts de version majeure).
    """
    root = Path(__file__).parent.parent.parent
    try:
        for rel in ("Dockerfile", "andre-mcp/Dockerfile"):
            # Ignorer les commentaires : ils citent légitimement l'ancienne commande
            content = "\n".join(
                line for line in (root / rel).read_text().splitlines()
                if not line.lstrip().startswith("#")
            )
            assert "-r pyproject.toml" not in content, (
                f"{rel} : résolution fraîche des dépendances (utiliser uv sync --locked)"
            )
            assert "uv sync --locked" in content, (
                f"{rel} : l'installation doit passer par `uv sync --locked`"
            )
            # Le venv doit rester hors de /app : le `chown -R appuser:appuser /app`
            # recopie sinon tout le venv dans une couche supplémentaire (+1,1 Go).
            if "chown -R appuser:appuser /app" in content:
                assert "UV_PROJECT_ENVIRONMENT=/opt/venv" in content, (
                    f"{rel} : venv sous /app alors qu'un `chown -R /app` suit "
                    "(poser UV_PROJECT_ENVIRONMENT=/opt/venv)"
                )
                assert "/app/.venv" not in content, (
                    f"{rel} : référence résiduelle à /app/.venv"
                )
        results.add("Dockerfiles install depuis le lock", True)
    except AssertionError as e:
        results.add("Dockerfiles install depuis le lock", False, str(e))
    except Exception as e:
        results.add("Dockerfiles install depuis le lock", False, f"{type(e).__name__}: {str(e)[:200]}")


def main():
    print("=" * 60)
    print("Tests CI — andre-mcp")
    print("=" * 60)

    # Reconstruire le DuckDB à partir des Parquet (portabilité)
    _ensure_fixture_db()
    print(f"Fixture : {'✓' if FIXTURE_DB.exists() else '✗'} {FIXTURE_DIR}")

    results = TestResult()

    # Unitaires (toujours)
    test_mcp_pin_below_v2(results)
    test_dockerfiles_install_from_lock(results)
    test_package_imports(results)
    test_config_env_override(results)
    test_server_structure(results)
    test_tool_descriptions_not_empty(results)
    test_datasets_catalog(results)
    test_sql_schema_resource(results)
    test_prompts_callable(results)
    test_stdio_protocol(results)

    # Intégration (fixture ou skip)
    test_fixture_db_queries(results)
    test_fixture_query_sql_tool(results)
    test_visualize_tool(results)
    test_create_data_table_tool(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
