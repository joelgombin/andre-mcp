#!/usr/bin/env python3
"""
Test du transport stdio JSON-RPC du serveur MCP.

Vérifie que le serveur répond correctement aux requêtes JSON-RPC
sur stdin/stdout (le protocole MCP standard).

Usage:
    uv run python tests/test_stdio.py
"""

import json
import subprocess
import sys
from pathlib import Path


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
        print("Résultats des tests — stdio")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


def _send_jsonrpc(messages: list[dict], timeout: int = 30) -> list[str]:
    """Envoie des messages JSON-RPC au serveur MCP via stdio.

    Utilise le protocole MCP : chaque message est précédé d'un header
    Content-Length: N\\r\\n\\r\\n suivi du JSON.
    """
    # Construire l'entrée au format newline-delimited JSON
    stdin_data = "\n".join(json.dumps(msg) for msg in messages) + "\n"

    # Lancer le serveur
    src_dir = str(Path(__file__).parent.parent / "src")
    proc = subprocess.run(
        [sys.executable, "-c", f"""
import sys
sys.path.insert(0, '{src_dir}')
from andre_mcp.server import create_server
server = create_server()
server.run(transport='stdio')
"""],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    # Parser les réponses (newline-delimited JSON)
    responses = []
    for line in proc.stdout.strip().split("\n"):
        line = line.strip()
        if line and line.startswith("{"):
            try:
                parsed = json.loads(line)
                # Ignorer les notifications (pas d'id)
                if "id" in parsed:
                    responses.append(line)
            except json.JSONDecodeError:
                pass

    return responses


def test_initialize(results: TestResult):
    """Le serveur doit répondre à initialize."""
    print("\n[1] initialize...")
    try:
        responses = _send_jsonrpc([
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
                "id": 1,
            }
        ])

        assert len(responses) >= 1, f"Aucune réponse ({len(responses)})"
        resp = json.loads(responses[0])
        assert "result" in resp, f"Pas de 'result' dans la réponse : {resp}"
        assert "serverInfo" in resp["result"], f"Pas de serverInfo : {resp['result']}"
        server_name = resp["result"]["serverInfo"]["name"]
        assert "andr" in server_name.lower(), f"Nom serveur inattendu : {server_name}"
        results.add("initialize", True, f"serverInfo.name = {server_name}")
    except subprocess.TimeoutExpired:
        results.add("initialize", False, "Timeout (30s)")
    except Exception as e:
        results.add("initialize", False, str(e)[:200])


def test_tools_list(results: TestResult):
    """Le serveur doit lister ses outils via tools/list."""
    print("\n[2] tools/list...")
    try:
        responses = _send_jsonrpc([
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
                "id": 1,
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            {
                "jsonrpc": "2.0",
                "method": "tools/list",
                "params": {},
                "id": 2,
            },
        ])

        # Chercher la réponse à id=2
        tools_resp = None
        for r in responses:
            parsed = json.loads(r)
            if parsed.get("id") == 2:
                tools_resp = parsed
                break

        assert tools_resp is not None, f"Pas de réponse pour tools/list. Réponses: {responses}"
        assert "result" in tools_resp, f"Pas de result : {tools_resp}"
        tools = tools_resp["result"]["tools"]
        assert len(tools) >= 5, f"Trop peu d'outils : {len(tools)}"

        tool_names = [t["name"] for t in tools]
        assert "query_elections" in tool_names, f"query_elections manquant : {tool_names}"
        results.add("tools/list", True, f"{len(tools)} outils : {', '.join(tool_names)}")
    except subprocess.TimeoutExpired:
        results.add("tools/list", False, "Timeout (30s)")
    except Exception as e:
        results.add("tools/list", False, str(e)[:200])


def main():
    print("=" * 60)
    print("Tests stdio — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_initialize(results)
    test_tools_list(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
