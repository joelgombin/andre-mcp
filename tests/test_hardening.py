#!/usr/bin/env python3
"""
Tests du durcissement « preflight » du serveur MCP (avant mise à disposition large).

Couvre :
1. Injection d'un LIMIT de sécurité sur query_sql (anti-matérialisation de
   millions de lignes).
2. Garde de concurrence : message gracieux quand le serveur est saturé.
3. Log d'usage : chaque appel d'outil émet une ligne (capturée par CapRover).

Indépendant de la base DuckDB (intercepte / pilote les helpers internes).

Usage:
    uv run python tests/test_hardening.py
"""

import asyncio
import logging
import sys
import threading
import time
from pathlib import Path

from prometheus_client import generate_latest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import andre_mcp.server as server_mod  # noqa: E402


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.results = []

    def add(self, name, passed, message="", skipped=False):
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
        print("Résultats — durcissement preflight")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


def test_limit_injected_on_bare_select(results: TestResult):
    print("\n[1] LIMIT injecté sur un SELECT sans limite...")
    try:
        out = server_mod._enforce_select_limit("SELECT * FROM general_results")
        assert "LIMIT" in out.upper(), f"LIMIT manquant : {out}"
        assert str(server_mod._SQL_ROW_CAP) in out, f"Plafond attendu : {out}"
        results.add("LIMIT injecté (SELECT nu)", True, out.replace(chr(10), " "))
    except Exception as e:
        results.add("LIMIT injecté (SELECT nu)", False, str(e))


def test_existing_limit_preserved(results: TestResult):
    print("\n[2] LIMIT existant préservé (y compris en minuscules)...")
    try:
        q1 = "SELECT id_election FROM elections_disponibles LIMIT 5"
        assert server_mod._enforce_select_limit(q1) == q1, "LIMIT majuscule modifié"
        q2 = "select id_election from elections_disponibles limit 3"
        assert server_mod._enforce_select_limit(q2) == q2, "LIMIT minuscule modifié"
        results.add("LIMIT existant préservé", True)
    except Exception as e:
        results.add("LIMIT existant préservé", False, str(e))


def test_limit_injected_with_cte(results: TestResult):
    print("\n[3] LIMIT injecté sur une CTE (WITH ...)...")
    try:
        q = "WITH t AS (SELECT 1 AS x) SELECT * FROM t"
        out = server_mod._enforce_select_limit(q)
        assert "LIMIT" in out.upper(), f"LIMIT manquant sur CTE : {out}"
        results.add("LIMIT injecté (CTE)", True)
    except Exception as e:
        results.add("LIMIT injecté (CTE)", False, str(e))


def test_trailing_semicolon_handled(results: TestResult):
    print("\n[4] Point-virgule final retiré avant le LIMIT...")
    try:
        out = server_mod._enforce_select_limit("SELECT * FROM communes;")
        # Pas de « ; » au milieu (qui casserait la requête une fois le LIMIT ajouté)
        assert ";" not in out.rstrip(), f"Point-virgule mal placé : {out}"
        assert "LIMIT" in out.upper(), f"LIMIT manquant : {out}"
        results.add("Point-virgule final géré", True)
    except Exception as e:
        results.add("Point-virgule final géré", False, str(e))


def test_no_limit_for_non_select(results: TestResult):
    print("\n[5] Pas de LIMIT pour PRAGMA / EXPLAIN / DESCRIBE...")
    try:
        for q in ["PRAGMA database_list", "EXPLAIN SELECT 1", "DESCRIBE communes", "SHOW TABLES"]:
            out = server_mod._enforce_select_limit(q)
            assert out.strip() == q.strip(), f"Requête modifiée à tort : {q} → {out}"
        results.add("Non-SELECT laissés intacts", True)
    except Exception as e:
        results.add("Non-SELECT laissés intacts", False, str(e))


def test_concurrency_guard_rejects_when_full(results: TestResult):
    print("\n[6] Garde de concurrence : message gracieux quand saturé...")
    saved_guard = server_mod._concurrency_guard
    saved_timeout = server_mod._ACQUIRE_TIMEOUT
    try:
        # Sémaphore de taille 1, déjà pris → la prochaine acquisition échoue vite
        server_mod._concurrency_guard = threading.BoundedSemaphore(1)
        server_mod._ACQUIRE_TIMEOUT = 0.1
        assert server_mod._concurrency_guard.acquire(), "Setup : acquisition initiale"

        # query_elections.py existe → on passe le check d'existence, puis l'acquisition
        # échoue (saturé) → message « occupé », SANS lancer de subprocess.
        out = server_mod._run_script("query_elections.py", ["--list-elections"])
        assert "trop de requêtes" in out.lower() or "réessayer" in out.lower(), \
            f"Message de surcharge attendu, obtenu : {out[:200]}"
        results.add("Garde de concurrence", True)
    except Exception as e:
        results.add("Garde de concurrence", False, str(e))
    finally:
        server_mod._concurrency_guard = saved_guard
        server_mod._ACQUIRE_TIMEOUT = saved_timeout


def test_usage_logger_emits(results: TestResult):
    print("\n[7] Log d'usage : une ligne sans arguments est émise...")
    captured = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = _Capture()
    server_mod._usage_logger.addHandler(handler)
    try:
        server_mod._log_usage("query_sql", "claude", "ok", time.monotonic())
        assert any("query_sql" in m for m in captured), f"Log non émis : {captured}"
        assert any("status=ok" in m for m in captured), f"Statut absent : {captured}"
        assert any("client=claude" in m for m in captured), f"Client absent : {captured}"
        assert all("SELECT" not in m for m in captured), f"Argument présent : {captured}"
        results.add("Log d'usage émis sans arguments", True)
    except Exception as e:
        results.add("Log d'usage émis sans arguments", False, str(e))
    finally:
        server_mod._usage_logger.removeHandler(handler)


def test_tool_metrics_recorded(results: TestResult):
    print("\n[8] Métriques Prometheus : appel, durée et concurrence...")
    try:
        server = server_mod.InstrumentedFastMCP("metrics-test")

        @server.tool()
        def ping() -> str:
            return "pong"

        before = server_mod._tool_calls.labels(
            tool="ping", status="ok", client="unknown"
        )._value.get()
        asyncio.run(server.call_tool("ping", {}))
        after = server_mod._tool_calls.labels(
            tool="ping", status="ok", client="unknown"
        )._value.get()

        payload = generate_latest().decode()
        assert after == before + 1, f"Compteur inattendu : {before} → {after}"
        assert "andre_mcp_tool_duration_seconds" in payload, "Histogramme absent"
        assert "andre_mcp_tool_calls_in_flight" in payload, "Gauge absent"
        results.add("Métriques Prometheus enregistrées", True)
    except Exception as e:
        results.add("Métriques Prometheus enregistrées", False, str(e))


def test_metrics_route_registered(results: TestResult):
    print("\n[9] Endpoint Prometheus : GET /metrics est enregistré...")
    try:
        app = server_mod.create_server().streamable_http_app()
        paths = {getattr(route, "path", None) for route in app.routes}
        assert "/metrics" in paths, f"Routes disponibles : {sorted(str(p) for p in paths)}"
        results.add("Endpoint /metrics enregistré", True)
    except Exception as e:
        results.add("Endpoint /metrics enregistré", False, str(e))


def main():
    print("=" * 60)
    print("Tests durcissement preflight — andre-mcp")
    print("=" * 60)

    results = TestResult()
    test_limit_injected_on_bare_select(results)
    test_existing_limit_preserved(results)
    test_limit_injected_with_cte(results)
    test_trailing_semicolon_handled(results)
    test_no_limit_for_non_select(results)
    test_concurrency_guard_rejects_when_full(results)
    test_usage_logger_emits(results)
    test_tool_metrics_recorded(results)
    test_metrics_route_registered(results)

    success = results.print_summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
