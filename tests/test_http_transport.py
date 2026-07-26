#!/usr/bin/env python3
"""
Tests pour le transport HTTP (Streamable HTTP) du serveur MCP.

Vérifie que le serveur peut démarrer en mode HTTP et répondre aux requêtes.

Usage:
    uv run python tests/test_http_transport.py
"""

import json
import subprocess
import sys
import time
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
        print("Résultats des tests — HTTP transport")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


def test_run_http_exists(results: TestResult):
    """La fonction run_http() doit exister."""
    print("\n[1] run_http() existe...")
    try:
        from andre_mcp.server import run_http

        import inspect
        sig = inspect.signature(run_http)
        params = list(sig.parameters.keys())
        assert "host" in params, f"Paramètre 'host' manquant : {params}"
        assert "port" in params, f"Paramètre 'port' manquant : {params}"
        results.add("run_http() existe", True, f"params: {params}")
    except Exception as e:
        results.add("run_http() existe", False, str(e))


def test_http_server_starts(results: TestResult):
    """Le serveur HTTP doit démarrer et répondre sur /mcp."""
    print("\n[2] Serveur HTTP démarre...")
    import socket

    # Trouver un port libre
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    src_dir = str(Path(__file__).parent.parent / "src")
    proc = subprocess.Popen(
        [sys.executable, "-c", f"""
import sys
sys.path.insert(0, '{src_dir}')
from andre_mcp.server import run_http
run_http(host='127.0.0.1', port={port})
"""],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Attendre que le serveur démarre
        import httpx

        started = False
        for _ in range(20):  # 20 tentatives, 0.5s chacune
            time.sleep(0.5)
            try:
                resp = httpx.get(f"http://127.0.0.1:{port}/mcp", timeout=2)
                started = True
                break
            except (httpx.ConnectError, httpx.ReadError):
                continue

        if not started:
            stderr = proc.stderr.read().decode()[:500] if proc.stderr else ""
            results.add("Serveur HTTP démarre", False, f"Timeout. stderr: {stderr}")
            return

        # Le serveur MCP peut retourner divers codes pour un GET
        # (405 Method Not Allowed, 406 Not Acceptable, etc.) — c'est OK,
        # ça prouve qu'il tourne et répond.
        assert resp.status_code in (200, 400, 405, 406), \
            f"Status inattendu: {resp.status_code}"

        metrics = httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=2)
        assert metrics.status_code == 200, f"Status /metrics: {metrics.status_code}"
        assert "andre_mcp_tool_calls" in metrics.text, "Métriques MCP absentes"
        results.add(
            "Serveur HTTP démarre",
            True,
            f"port={port}, status={resp.status_code}, metrics=200",
        )

    except ImportError:
        results.add("Serveur HTTP démarre", False, skipped=True,
                     message="httpx non disponible")
    except Exception as e:
        results.add("Serveur HTTP démarre", False, str(e)[:200])
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_http_initialize(results: TestResult):
    """Le serveur HTTP doit répondre à une requête initialize via POST."""
    print("\n[3] Initialize via HTTP POST...")
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        port = s.getsockname()[1]

    src_dir = str(Path(__file__).parent.parent / "src")
    proc = subprocess.Popen(
        [sys.executable, "-c", f"""
import sys
sys.path.insert(0, '{src_dir}')
from andre_mcp.server import run_http
run_http(host='127.0.0.1', port={port})
"""],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        import httpx

        # Attendre le démarrage
        started = False
        for _ in range(20):
            time.sleep(0.5)
            try:
                httpx.get(f"http://127.0.0.1:{port}/mcp", timeout=2)
                started = True
                break
            except (httpx.ConnectError, httpx.ReadError):
                continue

        if not started:
            results.add("Initialize via HTTP", False, "Timeout au démarrage")
            return

        # Envoyer une requête initialize
        resp = httpx.post(
            f"http://127.0.0.1:{port}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
                "id": 1,
            },
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            timeout=10,
        )

        assert resp.status_code == 200, f"Status: {resp.status_code}, body: {resp.text[:300]}"

        # La réponse peut être JSON direct ou SSE
        body = resp.text
        if body.startswith("event:") or body.startswith("data:"):
            # Parser SSE
            for line in body.split("\n"):
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if "result" in data:
                        assert "serverInfo" in data["result"], f"Pas de serverInfo: {data}"
                        results.add("Initialize via HTTP", True,
                                    f"serverInfo.name = {data['result']['serverInfo']['name']}")
                        return
            results.add("Initialize via HTTP", False, f"Pas de result dans SSE: {body[:300]}")
        else:
            # JSON direct
            data = resp.json()
            if isinstance(data, list):
                data = data[0]
            assert "result" in data, f"Pas de result: {data}"
            results.add("Initialize via HTTP", True,
                        f"serverInfo.name = {data['result']['serverInfo']['name']}")

    except ImportError:
        results.add("Initialize via HTTP", False, skipped=True,
                     message="httpx non disponible")
    except Exception as e:
        results.add("Initialize via HTTP", False, str(e)[:200])
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def main():
    print("=" * 60)
    print("Tests HTTP transport — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_run_http_exists(results)
    test_http_server_starts(results)
    test_http_initialize(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
