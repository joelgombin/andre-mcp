#!/usr/bin/env python3
"""
Tests pour la page 404 du serveur MCP HTTP.

Le domaine mcp.andre.vote sert à la fois des requêtes machine (protocole MCP
sur /mcp) et une landing HTML pour humains (GET /). La 404 doit donc négocier
le contenu :

1. Un navigateur (Accept: text/html) reçoit la page « procès-verbal de
   carence » avec André dépité.
2. Un client machine reçoit le `Not Found` texte brut historique — le
   comportement des clients MCP ne doit pas changer.
3. Les routes existantes (/, /metrics, /mcp) restent intactes.

Usage:
    uv run python tests/test_not_found.py
"""

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
        print("Résultats des tests — page 404 HTTP")
        print("=" * 60)
        for name, status, message in self.results:
            icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○"}[status]
            print(f"  {icon} {name}")
            if message:
                print(f"    {message}")
        print(f"\nTotal: {self.passed} réussis, {self.failed} échoués, {self.skipped} ignorés")
        return self.failed == 0


def _client():
    """Construit un client de test sur l'app HTTP du serveur MCP."""
    from starlette.testclient import TestClient

    from andre_mcp.server import build_http_app

    # Pas de context manager : on ne veut pas démarrer le session manager MCP,
    # seules les routes HTTP hors protocole sont testées ici.
    return TestClient(build_http_app(), raise_server_exceptions=False)


def test_html_404(results: TestResult):
    """Un navigateur reçoit la page 404 HTML."""
    print("\n[1] 404 HTML pour un navigateur...")
    try:
        resp = _client().get("/bla", headers={"Accept": "text/html,application/xhtml+xml"})

        assert resp.status_code == 404, f"Statut attendu 404, reçu {resp.status_code}"
        assert "text/html" in resp.headers["content-type"], (
            f"Content-Type attendu text/html, reçu {resp.headers['content-type']}"
        )
        body = resp.text
        assert "André" in body, "La page doit mentionner André"
        assert "andre-triste.png" in body, "La page doit afficher le portrait dépité"
        assert "404" in body, "La page doit annoncer l'erreur 404"

        results.add("404 HTML pour un navigateur", True)
    except AssertionError as e:
        results.add("404 HTML pour un navigateur", False, str(e))
    except Exception as e:
        results.add("404 HTML pour un navigateur", False, f"Erreur inattendue : {e}")


def test_plain_text_404_for_machines(results: TestResult):
    """Un client machine garde le `Not Found` texte historique."""
    print("\n[2] 404 texte pour un client machine...")
    try:
        client = _client()

        for accept in ("application/json", "*/*", None):
            headers = {"Accept": accept} if accept else {}
            resp = client.get("/bla", headers=headers)

            label = accept or "(aucun en-tête Accept)"
            assert resp.status_code == 404, f"{label} : statut {resp.status_code}"
            assert "text/plain" in resp.headers["content-type"], (
                f"{label} : Content-Type {resp.headers['content-type']} (text/plain attendu)"
            )
            assert resp.text == "Not Found", f"{label} : corps « {resp.text} »"

        results.add("404 texte pour un client machine", True)
    except AssertionError as e:
        results.add("404 texte pour un client machine", False, str(e))
    except Exception as e:
        results.add("404 texte pour un client machine", False, f"Erreur inattendue : {e}")


def test_accept_quality_values(results: TestResult):
    """Un `Accept` qui refuse explicitement le HTML (q=0) reçoit du texte."""
    print("\n[3] Qualités de l'en-tête Accept respectées...")
    try:
        client = _client()

        # text/html explicitement refusé malgré sa présence dans l'en-tête
        for accept in ("text/html;q=0,*/*", "text/html;q=0.0, application/json"):
            resp = client.get("/bla", headers={"Accept": accept})
            assert "text/plain" in resp.headers["content-type"], (
                f"« {accept} » doit donner du texte, reçu {resp.headers['content-type']}"
            )

        # HTML accepté même avec une qualité explicite non nulle ou via un joker
        for accept in ("text/html;q=0.9", "text/*", "*/*;q=0.8, text/html;q=0.9"):
            resp = client.get("/bla", headers={"Accept": accept})
            assert "text/html" in resp.headers["content-type"], (
                f"« {accept} » doit donner du HTML, reçu {resp.headers['content-type']}"
            )

        results.add("Qualités de l'en-tête Accept respectées", True)
    except AssertionError as e:
        results.add("Qualités de l'en-tête Accept respectées", False, str(e))
    except Exception as e:
        results.add("Qualités de l'en-tête Accept respectées", False, f"Erreur inattendue : {e}")


def test_existing_routes_intact(results: TestResult):
    """La landing et /metrics répondent toujours."""
    print("\n[4] Routes existantes intactes...")
    try:
        client = _client()

        landing = client.get("/")
        assert landing.status_code == 200, f"GET / → {landing.status_code}"
        assert "text/html" in landing.headers["content-type"], "GET / doit rester du HTML"

        metrics = client.get("/metrics")
        assert metrics.status_code == 200, f"GET /metrics → {metrics.status_code}"

        results.add("Routes existantes intactes", True)
    except AssertionError as e:
        results.add("Routes existantes intactes", False, str(e))
    except Exception as e:
        results.add("Routes existantes intactes", False, f"Erreur inattendue : {e}")


def test_mcp_route_not_swallowed(results: TestResult):
    """La route /mcp n'est pas capturée par le handler 404."""
    print("\n[5] Route /mcp préservée...")
    try:
        from andre_mcp.server import build_http_app

        paths = {getattr(r, "path", None) for r in build_http_app().routes}
        assert "/mcp" in paths, f"Route /mcp absente ; routes = {sorted(p for p in paths if p)}"

        results.add("Route /mcp préservée", True)
    except AssertionError as e:
        results.add("Route /mcp préservée", False, str(e))
    except Exception as e:
        results.add("Route /mcp préservée", False, f"Erreur inattendue : {e}")


def main():
    print("=" * 60)
    print("Tests — page 404 du serveur MCP HTTP")
    print("=" * 60)

    results = TestResult()

    test_html_404(results)
    test_plain_text_404_for_machines(results)
    test_accept_quality_values(results)
    test_existing_routes_intact(results)
    test_mcp_route_not_swallowed(results)

    return 0 if results.print_summary() else 1


if __name__ == "__main__":
    sys.exit(main())
