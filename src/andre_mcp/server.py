"""Serveur MCP André — analyse électorale française."""

import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.responses import Response

from andre_mcp import __version__

# Répertoire racine du projet André (parent de andre-mcp/)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# Bornes de troncature pour les messages d'erreur (éviter les murs de texte)
_MAX_ERROR_LINES = 15
_MAX_ERROR_CHARS = 1500

# ==========================================================================
# DURCISSEMENT « PREFLIGHT » (mise à disposition publique du serveur MCP)
# ==========================================================================

# --- Log d'usage : capturé par les logs du conteneur CapRover.
#     Permet de suivre l'usage réel sans conserver les arguments des outils.
_usage_logger = logging.getLogger("andre_mcp.usage")
if not _usage_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [andre-mcp] %(message)s"))
    _usage_logger.addHandler(_h)
    _usage_logger.setLevel(logging.INFO)
_usage_logger.propagate = False

# --- Métriques d'usage Prometheus. Les labels sont volontairement bornés :
#     aucun argument, identifiant de session ou nom de client arbitraire.
_tool_calls = Counter(
    "andre_mcp_tool_calls",
    "Nombre d'appels d'outils MCP.",
    ("tool", "status", "client"),
)
_tool_duration = Histogram(
    "andre_mcp_tool_duration_seconds",
    "Durée des appels d'outils MCP en secondes.",
    ("tool",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
_tool_in_flight = Gauge(
    "andre_mcp_tool_calls_in_flight",
    "Nombre d'appels d'outils MCP en cours.",
    ("tool",),
)

_CLIENT_CATEGORIES = {
    "claude": ("claude",),
    "mcp-remote": ("mcp-remote",),
    "codex": ("codex",),
    "cursor": ("cursor",),
    "vscode": ("visual studio code", "vscode"),
}

# --- Garde de concurrence : borne le nombre d'exécutions lourdes simultanées
#     (subprocess + requêtes SQL) pour éviter de saturer le CPU lors d'un pic
#     de trafic. Configurable via l'environnement.
_MAX_CONCURRENCY = int(os.environ.get("ANDRE_MCP_MAX_CONCURRENCY", "8"))
_ACQUIRE_TIMEOUT = float(os.environ.get("ANDRE_MCP_ACQUIRE_TIMEOUT", "20"))
_concurrency_guard = threading.BoundedSemaphore(_MAX_CONCURRENCY)
_BUSY_MESSAGE = (
    "Erreur : le serveur André traite trop de requêtes simultanément. "
    "Merci de réessayer dans quelques instants."
)

# --- Plafond de lignes pour query_sql : un LIMIT est injecté sur les SELECT/WITH
#     sans limite explicite, pour éviter de matérialiser des millions de lignes
#     dans un DataFrame pandas (saturation mémoire/CPU).
_SQL_ROW_CAP = int(os.environ.get("ANDRE_MCP_SQL_ROW_CAP", "5000"))


def _log_usage(tool: str, client: str, status: str, start: float) -> None:
    """Émet une ligne de log d'usage sans argument ni contenu utilisateur."""
    dur_ms = int((time.monotonic() - start) * 1000)
    _usage_logger.info(
        "tool=%s client=%s status=%s dur=%dms", tool, client, status, dur_ms
    )


def _client_category(server: FastMCP) -> str:
    """Retourne une catégorie bornée à partir du clientInfo MCP."""
    try:
        params = server.get_context().request_context.session.client_params
        raw_name = params.clientInfo.name if params else ""
    except (AttributeError, LookupError, RuntimeError, ValueError):
        return "unknown"

    name = raw_name.lower()
    for category, markers in _CLIENT_CATEGORIES.items():
        if any(marker in name for marker in markers):
            return category
    return "other" if name else "unknown"


def _result_is_error(result: Any) -> bool:
    """Détecte les erreurs métier retournées sous forme de contenu MCP."""
    items = result if isinstance(result, (list, tuple)) else (result,)
    for item in items:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.lstrip().lower().startswith(
            ("erreur", "error")
        ):
            return True
    return False


class InstrumentedFastMCP(FastMCP):
    """FastMCP avec métriques centralisées pour tous les appels d'outils."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        start = time.monotonic()
        client = _client_category(self)
        status = "error"
        _tool_in_flight.labels(tool=name).inc()
        try:
            result = await super().call_tool(name, arguments)
            status = "error" if _result_is_error(result) else "ok"
            return result
        finally:
            _tool_calls.labels(tool=name, status=status, client=client).inc()
            _tool_duration.labels(tool=name).observe(time.monotonic() - start)
            _tool_in_flight.labels(tool=name).dec()
            _log_usage(name, client, status, start)


def _enforce_select_limit(query: str) -> str:
    """Ajoute un LIMIT de sécurité aux SELECT/WITH sans limite explicite.

    Empêche un `SELECT * FROM general_results` (3M+ lignes) de matérialiser tout
    le résultat avant troncature. Les requêtes déjà bornées, ou non-SELECT
    (PRAGMA, EXPLAIN, SHOW, DESCRIBE), sont laissées intactes.
    """
    q = query.strip().rstrip(";").strip()
    upper = q.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return q
    if re.search(r"\bLIMIT\b", upper):
        return q
    return f"{q}\nLIMIT {_SQL_ROW_CAP}"


def _format_script_error(script_name: str, returncode: int, stderr: str) -> str:
    """Construit un message d'erreur lisible à partir du stderr d'un script.

    - Reformule les erreurs argparse courantes en messages explicites.
    - Pour les autres erreurs, conserve le message *complet* (cause racine +
      étapes de remédiation), en filtrant uniquement le bruit du traceback
      Python. Ne réduit JAMAIS le message à sa seule dernière ligne — c'était
      le bug qui transformait l'erreur sociodem multi-lignes en un absurde
      « Erreur :   3. uv run python setup_duckdb.py --rebuild ».
    """
    stderr = (stderr or "").strip()

    # --- Reformulations argparse en messages lisibles ---
    if "error: the following arguments are required" in stderr:
        missing = stderr.split("required:")[-1].strip()
        return (
            f"Erreur : paramètres manquants pour {script_name} : {missing}\n"
            f"Vérifiez les paramètres de l'outil."
        )
    if "error: argument" in stderr and "invalid choice" in stderr:
        return f"Erreur : valeur invalide. {stderr.split('error:')[-1].strip()}"
    if "error: unrecognized arguments" in stderr:
        unknown = stderr.split("unrecognized arguments:")[-1].strip()
        return f"Erreur : paramètres non reconnus : {unknown}"

    # --- Autres erreurs : message complet, sans le bruit du traceback Python ---
    lines = [
        ln for ln in stderr.split("\n")
        if ln.strip()
        and not ln.startswith("Traceback")
        and not ln.lstrip().startswith('File "')
    ]
    if not lines:
        return f"Erreur : {script_name} a échoué (code {returncode})."

    # La partie utile (exception finale + conseils) se trouve en fin de sortie :
    # on garde la queue si le message est très long, jamais une seule ligne.
    if len(lines) > _MAX_ERROR_LINES:
        lines = ["…(début tronqué)"] + lines[-_MAX_ERROR_LINES:]
    message = "\n".join(lines).strip()
    if len(message) > _MAX_ERROR_CHARS:
        message = "…(tronqué)\n" + message[-_MAX_ERROR_CHARS:].lstrip()

    # Éviter un double préfixe « Erreur : Erreur : » si le script préfixe déjà.
    if message.lstrip().lower().startswith(("erreur", "error", "❌")):
        return message
    return f"Erreur : {message}"


def _run_script(script_name: str, args: list[str]) -> str:
    """Exécute un script query du projet André via subprocess.

    Args:
        script_name: Nom du script (ex: "query_elections.py")
        args: Arguments CLI

    Returns:
        Sortie stdout du script. En cas d'erreur, retourne un message
        d'erreur lisible (pas de traceback Python brut).
    """
    script_path = PROJECT_ROOT / script_name
    if not script_path.exists():
        return f"Erreur : script introuvable ({script_name}). Vérifiez l'installation d'André."

    # Garde de concurrence : protège le CPU lors d'un pic de trafic.
    if not _concurrency_guard.acquire(timeout=_ACQUIRE_TIMEOUT):
        return _BUSY_MESSAGE

    cmd = [sys.executable, str(script_path)] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return f"Erreur : le script {script_name} a dépassé le timeout (120s)."
    finally:
        _concurrency_guard.release()

    if result.returncode != 0:
        return _format_script_error(script_name, result.returncode, result.stderr)

    return result.stdout.strip()


def create_server() -> FastMCP:
    """Crée et configure le serveur MCP André.

    Returns:
        Instance FastMCP configurée avec outils, resources et prompts.
    """
    mcp = InstrumentedFastMCP(
        "andré",
        instructions=(
            "André est un agent d'analyse électorale française. "
            "Il donne accès aux résultats de 55 élections (1999-2026) "
            "au niveau bureau de vote, avec des données socio-démographiques, "
            "des cartes électorales et des outils d'analyse avancés."
        ),
    )

    # ==========================================
    # OUTILS
    # ==========================================

    _register_tools(mcp)

    # ==========================================
    # RESOURCES
    # ==========================================

    _register_resources(mcp)

    # ==========================================
    # PROMPTS
    # ==========================================

    _register_prompts(mcp)

    # ==========================================
    # LANDING PAGE
    # ==========================================

    _register_landing_page(mcp)

    # ==========================================
    # OBSERVABILITÉ
    # ==========================================

    _register_metrics_route(mcp)

    return mcp


def _register_tools(mcp: FastMCP) -> None:
    """Enregistre les outils MCP."""

    @mcp.tool(
        title="Résultats électoraux",
        annotations=ToolAnnotations(
            title="Résultats électoraux",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def query_elections(
        action: str,
        election_id: str | None = None,
        commune_code: str | None = None,
        departement_code: str | None = None,
        region_code: str | None = None,
        bureau_id: str | None = None,
        bureau_code: str | None = None,
        candidate_name: str | None = None,
        family_name: str | None = None,
        by_family: bool = False,
        rank_metric: str | None = None,
        order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        use_ferd_coding: bool = False,
        nuance_level: int | None = None,
        circonscription_code: str | None = None,
        canton_code: str | None = None,
        candidate_query: str | None = None,
        candidate_search: str | None = None,
        list_search: str | None = None,
        nuance_code: str | None = None,
        scrutin_type: str | None = None,
        tour: int | None = None,
        compare_to_election: str | None = None,
        famille: str | None = None,
        elections: str | None = None,
        commune_codes: str | None = None,
        epci: str | None = None,
        population_min: int | None = None,
        population_max: int | None = None,
        commune_metric: str | None = None,
        condition: str | None = None,
        sort_by: str | None = None,
        top_n: int | None = None,
        with_sociodem: bool = False,
    ) -> str:
        """Requête les données électorales françaises (1999-2026).

        Niveaux territoriaux : NATIONAL (aucun filtre), RÉGIONAL (region_code),
        DÉPARTEMENTAL (departement_code), COMMUNAL (commune_code).

        Actions de base : list_elections, query_results, list_bureaux, query_bureau,
        query_by_bureau, rank_bureaux, list_circonscriptions, query_circonscription,
        list_cantons, query_canton, search_candidate, query_candidatures, etc.

        Actions « panel » (1 appel = analyse complète, évite les boucles) :
        - compare_elections : compare deux scrutins pour un territoire
          (election_id + compare_to_election + commune_code|departement_code,
          candidate_name optionnel).
        - query_all_circonscriptions : vainqueur de CHAQUE circonscription d'un
          territoire en un appel (election_id + commune_code|departement_code,
          législatives uniquement).
        - vote_reserve : score d'une famille + abstention par bureau de vote sur
          les scrutins récents comparables (commune_code + famille).
        - select_communes : sélectionne/classe des communes par critères géo +
          population + électoraux (region_code/departement_code/epci/commune_codes,
          population_min/max, commune_metric, condition, sort_by, limit, order ;
          commune_metric/condition nécessitent election_id).
        - cross_election_panel : évolution d'un sujet (famille ou nuance_code) par
          commune sur N scrutins (elections=ids séparés par des virgules + scope
          commune_code/departement_code/region_code/commune_codes).
        - bureau_breakdown : pivot par bureau (top_n nuances/blocs + participation,
          with_sociodem optionnel) pour commune_code + election_id.
        """
        # --- Actions « panel » : chacune construit ses propres flags et retourne
        #     directement, en miroir du dispatch de l'agent principal (main.py). ---
        if action == "compare_elections":
            if not election_id or not compare_to_election:
                return ("Erreur : election_id et compare_to_election sont requis "
                        "pour compare_elections.")
            panel_args = ["--election", election_id, "--compare-to", compare_to_election]
            if commune_code:
                panel_args.extend(["--commune", commune_code])
            elif departement_code:
                panel_args.extend(["--departement", departement_code])
            else:
                return ("Erreur : commune_code ou departement_code est requis "
                        "pour compare_elections.")
            if candidate_name:
                panel_args.extend(["--candidate", candidate_name])
            return _run_script("query_elections.py", panel_args)

        if action == "query_all_circonscriptions":
            if not election_id:
                return ("Erreur : election_id est requis pour "
                        "query_all_circonscriptions (législatives uniquement).")
            panel_args = ["--election", election_id, "--all-circonscriptions"]
            if commune_code:
                panel_args.extend(["--commune", commune_code])
            elif departement_code:
                panel_args.extend(["--departement", departement_code])
            else:
                return ("Erreur : commune_code ou departement_code est requis "
                        "pour query_all_circonscriptions.")
            return _run_script("query_elections.py", panel_args)

        if action == "vote_reserve":
            if not commune_code or not famille:
                return ("Erreur : commune_code et famille sont requis pour "
                        "vote_reserve (famille = Gauche, Centre, Droite, "
                        "« Extrême droite » ou Autre).")
            return _run_script("query_elections.py", [
                "--vote-reserve", "--commune", commune_code,
                "--famille", famille, "--json",
            ])

        if action == "select_communes":
            panel_args = ["--select-communes", "--json"]
            if region_code:
                panel_args.extend(["--region", region_code])
            if departement_code:
                panel_args.extend(["--departement", departement_code])
            if epci:
                panel_args.extend(["--epci", epci])
            if commune_codes:
                panel_args.extend(["--commune-codes", commune_codes])
            if population_min is not None:
                panel_args.extend(["--population-min", str(population_min)])
            if population_max is not None:
                panel_args.extend(["--population-max", str(population_max)])
            if election_id:
                panel_args.extend(["--election", election_id])
            if commune_metric:
                panel_args.extend(["--commune-metric", commune_metric])
            if condition:
                panel_args.extend(["--condition", condition])
            if sort_by:
                panel_args.extend(["--sort-by", sort_by])
            if limit is not None:
                panel_args.extend(["--limit", str(limit)])
            if order:
                panel_args.extend(["--order", order])
            return _run_script("query_elections.py", panel_args)

        if action == "cross_election_panel":
            if not elections:
                return ("Erreur : elections (ids séparés par des virgules) est "
                        "requis pour cross_election_panel.")
            if not (famille or nuance_code):
                return ("Erreur : famille ou nuance_code est requis pour "
                        "cross_election_panel.")
            panel_args = ["--cross-election", "--json", "--elections", elections]
            if commune_code:
                panel_args.extend(["--commune", commune_code])
            if departement_code:
                panel_args.extend(["--departement", departement_code])
            if region_code:
                panel_args.extend(["--region", region_code])
            if commune_codes:
                panel_args.extend(["--commune-codes", commune_codes])
            if famille:
                panel_args.extend(["--famille", famille])
            if nuance_code:
                panel_args.extend(["--nuance", nuance_code])
            return _run_script("query_elections.py", panel_args)

        if action == "bureau_breakdown":
            if not commune_code or not election_id:
                return ("Erreur : commune_code et election_id sont requis pour "
                        "bureau_breakdown.")
            panel_args = ["--bureau-breakdown", "--json",
                          "--commune", commune_code, "--election", election_id]
            if top_n is not None:
                panel_args.extend(["--top-n", str(top_n)])
            if with_sociodem:
                panel_args.append("--with-sociodem")
            return _run_script("query_elections.py", panel_args)

        args = []

        # Mapping action → flags CLI
        if action == "list_elections":
            args.append("--list-elections")
        elif action == "list_ferd_elections":
            args.append("--list-ferd-elections")
        else:
            if election_id:
                args.extend(["--election", election_id])

        if commune_code:
            args.extend(["--commune", commune_code])
        if departement_code:
            args.extend(["--departement", departement_code])
        if region_code:
            args.extend(["--region", region_code])
        if bureau_id:
            args.extend(["--bureau", bureau_id])
        if bureau_code:
            args.extend(["--bureau-code", bureau_code])
        if candidate_name:
            args.extend(["--candidate", candidate_name])
        if family_name:
            args.extend(["--family", family_name])
        if by_family:
            args.append("--by-family")
        if with_sociodem:
            args.append("--with-sociodem")
        if rank_metric:
            args.extend(["--metric", rank_metric])
        if order:
            args.extend(["--order", order])
        if limit is not None:
            args.extend(["--limit", str(limit)])
        if offset is not None:
            args.extend(["--offset", str(offset)])
        if use_ferd_coding:
            args.append("--ferd-nuance")
        if nuance_level is not None:
            args.extend(["--nuance-level", str(nuance_level)])
        if circonscription_code:
            args.extend(["--circonscription", circonscription_code])
        if canton_code:
            args.extend(["--canton", canton_code])

        # Actions spécifiques
        if action == "query_by_bureau":
            args.append("--by-bureau")
        elif action == "rank_bureaux":
            args.append("--rank-bureaux")
        elif action == "list_bureaux":
            args.append("--list-bureaux")
        elif action == "list_cantons":
            args.append("--list-cantons")
        elif action == "list_circonscriptions":
            args.append("--list-circonscriptions")
        elif action == "search_candidate":
            if candidate_query:
                args.extend(["--search-candidate", candidate_query])
        elif action == "query_candidatures":
            args.append("--query-candidatures")
            if candidate_search:
                args.extend(["--candidate-search", candidate_search])
            if list_search:
                args.extend(["--list-search", list_search])
            if nuance_code:
                args.extend(["--nuance-code", nuance_code])
            if scrutin_type:
                args.extend(["--scrutin-type", scrutin_type])
        elif action == "list_candidature_elections":
            args.append("--list-candidature-elections")

        return _run_script("query_elections.py", args)

    @mcp.tool(
        title="Divisions administratives",
        annotations=ToolAnnotations(
            title="Divisions administratives",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def query_admin(
        action: str,
        search_term: str | None = None,
        commune_code: str | None = None,
        postal_code: str | None = None,
        departement_code: str | None = None,
        epci_code: str | None = None,
    ) -> str:
        """Requête les divisions administratives françaises.

        Actions : search_commune, get_commune, search_postal,
        list_epci, get_epci, list_cantons, list_departements, list_regions.
        """
        args = []

        if action == "search_commune" and search_term:
            args.extend(["--search", search_term])
        elif action == "get_commune" and commune_code:
            args.extend(["--commune", commune_code])
        elif action == "search_postal" and postal_code:
            args.extend(["--postal-code", postal_code])
        elif action == "list_epci":
            args.append("--list-epci")
            if departement_code:
                args.extend(["--departement", departement_code])
        elif action == "get_epci" and epci_code:
            args.extend(["--epci", epci_code])
        elif action == "list_cantons":
            args.append("--list-cantons")
            if departement_code:
                args.extend(["--departement", departement_code])
        elif action == "list_departements":
            args.append("--list-departements")
        elif action == "list_regions":
            args.append("--list-regions")

        return _run_script("query_admin.py", args)

    @mcp.tool(
        title="Élus de la République",
        annotations=ToolAnnotations(
            title="Élus de la République",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def query_elus(
        action: str,
        search_term: str | None = None,
        mandate_type: str | None = None,
        departement_code: str | None = None,
    ) -> str:
        """Requête les élus de la République française (base BRÉF).

        Actions : search_elu, list_mandate_types, list_elus.
        """
        args = []

        if action == "search_elu" and search_term:
            args.extend(["--search", search_term])
        elif action == "list_mandate_types":
            args.append("--list-mandate-types")
        elif action == "list_elus":
            args.append("--list-elus")
            if mandate_type:
                args.extend(["--mandate-type", mandate_type])
            if departement_code:
                args.extend(["--departement", departement_code])

        return _run_script("query_elus.py", args)

    @mcp.tool(
        title="Données socio-démographiques",
        annotations=ToolAnnotations(
            title="Données socio-démographiques",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def query_sociodem(
        action: str,
        commune_code: str | None = None,
        variable: str | None = None,
        commune_codes: str | None = None,
        order: str | None = None,
    ) -> str:
        """Requête les données socio-démographiques au niveau bureau de vote.

        Données INSEE RP 2022 + Filosofi 2021, interpolées IRIS→BDV.
        Actions : list_variables, profile, indices, rank, compare.
        """
        args = []

        if action == "list_variables":
            args.append("--list-variables")
        elif action == "profile" and commune_code:
            args.extend(["--commune", commune_code, "--profile"])
        elif action == "indices" and commune_code:
            args.extend(["--commune", commune_code, "--indices"])
        elif action == "rank" and commune_code and variable:
            args.extend(["--commune", commune_code, "--rank-by", variable])
            if order:
                args.extend(["--order", order])
        elif action == "compare" and commune_codes:
            args.extend(["--compare", commune_codes])

        return _run_script("query_sociodem.py", args)

    @mcp.tool(
        title="Profil social des candidats municipaux",
        annotations=ToolAnnotations(
            title="Profil social des candidats municipaux",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def query_dlp(
        action: str,
        commune_code: str | None = None,
        departement_code: str | None = None,
        classe: str | None = None,
        sexe: str | None = None,
        tetes_only: bool = False,
        sortants_only: bool = False,
        search: str | None = None,
    ) -> str:
        """Requête les données DLP (composition sociale des candidats municipaux 2020).

        ~546 000 candidats dans ~10 000 communes (≥1 000 hab.).
        Classes : CDOM (dominant), CMOY (moyen), CSUB (subalterne), RET (retraité), AUTRES.
        Actions : summary, composition, parite, listes, candidats, professions, sortants.
        """
        args = ["--json"]

        action_flags = {
            "summary": "--summary",
            "composition": "--composition",
            "parite": "--parite",
            "listes": "--listes",
            "candidats": "--candidats",
            "professions": "--professions",
            "sortants": "--sortants",
        }
        flag = action_flags.get(action)
        if not flag:
            return f"Erreur : action inconnue '{action}'"
        args.append(flag)

        if commune_code:
            args.extend(["--commune", commune_code])
        if departement_code:
            args.extend(["--departement", departement_code])
        if classe:
            args.extend(["--classe", classe])
        if sexe:
            args.extend(["--sexe", sexe])
        if tetes_only:
            args.append("--tetes-only")
        if sortants_only:
            args.append("--sortants-only")
        if search:
            args.extend(["--search", search])

        return _run_script("query_dlp.py", args)

    @mcp.tool(
        title="Carte électorale",
        annotations=ToolAnnotations(
            title="Carte électorale",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def generate_map(
        election_id: str,
        metric: str = "family",
        geography: str = "bv",
        department: str | None = None,
        commune: str | None = None,
        region: str | None = None,
        nuance: str | None = None,
        circonscription: str | None = None,
        canton: str | None = None,
    ) -> str:
        """Génère une carte électorale (PNG) pour une élection.

        Paramètres :
        - election_id : identifiant élection (ex: '2024_legi_t1', '2020_muni_t2')
        - metric : 'family' (famille politique en tête) ou 'nuance' (score d'une nuance)
        - geography : 'bv' (bureau de vote, défaut) ou 'commune' (carte nationale)
        - department : code département (ex: '13'). Requis pour geography='bv' sauf si commune fourni.
        - commune : code commune (ex: '13055' pour Marseille). Centre la carte sur cette commune.
        - nuance : code nuance quand metric='nuance' (ex: 'RN', 'LFI', 'ENS')
        - region : code région pour geography='commune' (ex: '93' pour PACA)

        Exemples :
        - Carte par famille, Bouches-du-Rhône : election_id='2024_legi_t1', department='13'
        - Carte score RN, Marseille : election_id='2024_legi_t1', commune='13055', metric='nuance', nuance='RN'
        - Carte nationale : election_id='2024_euro_t1', geography='commune'
        """
        args = ["--election", election_id, "--metric", metric, "--geography", geography]
        if department:
            args.extend(["--department", department])
        if commune:
            args.extend(["--commune", commune])
        if region:
            args.extend(["--region", region])
        if nuance:
            args.extend(["--nuance", nuance])
        if circonscription:
            args.extend(["--circonscription", circonscription])
        if canton:
            args.extend(["--canton", canton])
        args.append("--json")

        return _run_script("generate_map.py", args)

    @mcp.tool(
        title="Simulation de fusions de listes",
        annotations=ToolAnnotations(
            title="Simulation de fusions de listes",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def simulate_fusions(
        action: str,
        election_id: str,
        commune_code: str,
        report_rate: float = 0.75,
    ) -> str:
        """Simule les scénarios de fusion de listes municipales entre T1 et T2.

        Actions : 'status' (classer les listes après T1), 'simulate' (projections T2).
        Uniquement pour les élections municipales.
        """
        args = ["--election", election_id, "--commune", commune_code]

        if action == "status":
            args.append("--status")
        elif action == "simulate":
            args.append("--simulate")
            args.extend(["--report-rate", str(report_rate)])
        else:
            return f"Erreur : action inconnue '{action}'. Utiliser 'status' ou 'simulate'."

        return _run_script("fusion_simulator.py", args)

    @mcp.tool(
        title="Export de données",
        annotations=ToolAnnotations(
            title="Export de données",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def export_data(
        filename: str,
        file_format: str = "csv",
        data: str | None = None,
        source_path: str | None = None,
    ) -> str:
        """Exporte des données électorales vers un fichier téléchargeable (CSV, JSON, TSV).

        Deux modes : data (contenu inline) ou source_path (chemin vers fichier existant).
        """
        import tempfile
        from pathlib import Path as P

        if not data and not source_path:
            return "Erreur : fournir 'data' ou 'source_path'."

        # Détermine le répertoire de sortie
        output_dir = P.home() / "Downloads"
        output_dir.mkdir(exist_ok=True)

        ext = {"csv": ".csv", "json": ".json", "tsv": ".tsv"}.get(file_format, ".csv")
        output_path = output_dir / f"{filename}{ext}"

        if source_path:
            import shutil
            src = P(source_path)
            if not src.exists():
                return f"Erreur : fichier source introuvable : {source_path}"
            shutil.copy2(str(src), str(output_path))
        else:
            output_path.write_text(data, encoding="utf-8")

        size_kb = output_path.stat().st_size / 1024
        return f"Fichier exporté : {output_path} ({size_kb:.1f} Ko)"

    @mcp.tool(
        title="Visualisation graphique",
        annotations=ToolAnnotations(
            title="Visualisation graphique",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def visualize(
        chart_type: str | None = None,
        data: list[dict] | None = None,
        data_file: str | None = None,
        spec: dict | None = None,
        x: str | None = None,
        y: str | None = None,
        color: str | None = None,
        title: str | None = None,
    ) -> str:
        """Crée une visualisation Plotly (retourne le spec JSON).

        Deux modes :
        - Spec libre : fournir 'spec' (dict Plotly complet {data, layout})
        - Assisté : fournir chart_type + data + x + y

        Types supportés : bar, line, scatter, histogram, box, violin, heatmap, treemap.
        """
        import json as json_mod

        if spec:
            return json_mod.dumps(spec, ensure_ascii=False, indent=2)

        if not chart_type or (not data and not data_file):
            return "Erreur : fournir 'spec' (mode libre) ou 'chart_type' + 'data'/'data_file' (mode assisté)."

        # Charger les données depuis fichier si nécessaire
        if data_file:
            from pathlib import Path as P
            p = P(data_file)
            if not p.exists():
                return f"Erreur : fichier introuvable : {data_file}"
            data = json_mod.loads(p.read_text(encoding="utf-8"))

        # Construire un spec Plotly simplifié
        plotly_spec = {
            "data": [{"type": chart_type, "x": [], "y": []}],
            "layout": {"title": title or ""},
        }
        if data and x and y:
            plotly_spec["data"][0]["x"] = [row.get(x) for row in data]
            plotly_spec["data"][0]["y"] = [row.get(y) for row in data]
            if color:
                plotly_spec["data"][0]["marker"] = {
                    "color": [row.get(color) for row in data]
                }

        return json_mod.dumps(plotly_spec, ensure_ascii=False, indent=2)

    @mcp.tool(
        title="Tableau de données",
        annotations=ToolAnnotations(
            title="Tableau de données",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def create_data_table(
        title: str,
        columns: list[dict],
        rows: list[dict],
        description: str | None = None,
    ) -> str:
        """Crée un tableau de données formaté à partir de données structurées.

        Utile pour présenter des résultats calculés ou combinés.
        Retourne un tableau texte formaté.
        """
        if not columns or not rows:
            return "Erreur : columns et rows sont requis."

        # Construire un tableau texte formaté
        keys = [col["key"] for col in columns]
        labels = [col.get("label", col["key"]) for col in columns]

        # Calculer les largeurs de colonnes
        widths = [len(label) for label in labels]
        for row in rows[:100]:  # Limiter pour les très grands tableaux
            for i, key in enumerate(keys):
                val = str(row.get(key, ""))
                widths[i] = max(widths[i], len(val))

        # Header
        header = " | ".join(label.ljust(widths[i]) for i, label in enumerate(labels))
        separator = "-+-".join("-" * w for w in widths)

        lines = [f"# {title}"]
        if description:
            lines.append(description)
        lines.extend(["", header, separator])

        for row in rows[:500]:  # Limiter à 500 lignes
            line = " | ".join(
                str(row.get(key, "")).ljust(widths[i])
                for i, key in enumerate(keys)
            )
            lines.append(line)

        if len(rows) > 500:
            lines.append(f"\n... ({len(rows)} lignes au total, 500 affichées)")

        return "\n".join(lines)

    @mcp.tool(
        title="Requête SQL (lecture seule)",
        annotations=ToolAnnotations(
            title="Requête SQL (lecture seule)",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def query_sql(query: str) -> str:
        """Exécute une requête SQL directe sur la base DuckDB (read-only).

        Tables principales :
        - general_results : résultats généraux par bureau de vote
        - candidats_results : résultats par candidat par bureau de vote
        - elections_disponibles : liste des élections (id_election, nb_bureaux, total_inscrits)
        - candidats_avec_nuances : candidats avec famille politique (si nuances enrichies)
        - resultats_par_famille : résultats agrégés par famille politique
        - communes, epci, departements, regions : divisions administratives
        - bv_sociodemographique : données socio-démo par bureau de vote

        ATTENTION — colonnes de general_results (noms avec espaces, à quoter) :
        id_election, id_brut_miom, "Code du département", "Code de la commune" (SANS préfixe dept, ex: '055' pour Marseille),
        "Libellé de la commune", Inscrits, Abstentions, Votants, Blancs, Nuls, Exprimés.

        PRÉFÉRER query_elections pour les analyses standard — query_sql est pour les requêtes ad hoc avancées.

        Exemple :
        SELECT id_election, ROUND(100.0 * SUM(Abstentions) / NULLIF(SUM(Inscrits), 0), 1) as abstention_pct
        FROM general_results WHERE "Code du département" = '13' AND "Code de la commune" = '055'
        GROUP BY id_election ORDER BY id_election DESC LIMIT 5
        """
        from andre_mcp.db import close_connection, get_connection

        from andre_mcp.config import get_data_dir

        # Utilise la DB du projet parent si elle existe, sinon celle du data dir
        project_db = PROJECT_ROOT / "data" / "elections" / "elections.duckdb"
        if project_db.exists():
            db_path = project_db
        else:
            db_path = get_data_dir() / "elections.duckdb"

        con = get_connection(db_path=db_path)

        # Sécurité : bloque les requêtes d'écriture (en plus du read-only DB).
        query_upper = query.strip().upper()
        forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]
        for keyword in forbidden:
            if query_upper.startswith(keyword):
                return f"Erreur : les requêtes {keyword} ne sont pas autorisées (read-only)."

        # Garde de concurrence : protège le CPU lors d'un pic de trafic.
        if not _concurrency_guard.acquire(timeout=_ACQUIRE_TIMEOUT):
            return _BUSY_MESSAGE

        # LIMIT de sécurité : évite de matérialiser des millions de lignes.
        safe_query = _enforce_select_limit(query)
        try:
            df = con.execute(safe_query).df()
            if len(df) == 0:
                return "Aucun résultat."
            if len(df) > 500:
                return f"Résultats tronqués ({len(df)} lignes, affichage des 500 premières) :\n\n{df.head(500).to_string(index=False)}"
            return df.to_string(index=False)
        except Exception as e:
            return f"Erreur SQL : {e}"
        finally:
            _concurrency_guard.release()


def _register_resources(mcp: FastMCP) -> None:
    """Enregistre les resources MCP (références documentaires)."""

    # Répertoire des références du projet
    refs_dir = PROJECT_ROOT / "references"
    skills_dir = PROJECT_ROOT / ".claude" / "skills" / "electoral-analysis"

    @mcp.resource("andre://references/methodologies")
    def methodologies() -> str:
        """Méthodologies d'analyse électorale (réserves de voix, typologies, PCA)."""
        path = refs_dir / "methodologies.md"
        return path.read_text(encoding="utf-8") if path.exists() else "Fichier introuvable."

    @mcp.resource("andre://references/data_schema")
    def data_schema() -> str:
        """Schéma des données électorales (3M+ lignes, 1999-2026)."""
        path = refs_dir / "data_schema.md"
        return path.read_text(encoding="utf-8") if path.exists() else "Fichier introuvable."

    @mcp.resource("andre://references/political_colors")
    def political_colors() -> str:
        """Couleurs officielles des familles et nuances politiques (JSON)."""
        path = refs_dir / "political_colors.json"
        return path.read_text(encoding="utf-8") if path.exists() else "{}"

    @mcp.resource("andre://references/election_dates")
    def election_dates() -> str:
        """Dates et métadonnées des 55 élections (1999-2026, JSON)."""
        path = refs_dir / "election_dates.json"
        return path.read_text(encoding="utf-8") if path.exists() else "{}"

    @mcp.resource("andre://references/victory_rules")
    def victory_rules() -> str:
        """Règles de victoire par type d'élection."""
        path = refs_dir / "victory_rules.md"
        return path.read_text(encoding="utf-8") if path.exists() else "Fichier introuvable."

    @mcp.resource("andre://references/nuances")
    def nuances() -> str:
        """~90 codes de nuance politique du Ministère de l'Intérieur."""
        path = refs_dir / "nuances_reference.md"
        return path.read_text(encoding="utf-8") if path.exists() else "Fichier introuvable."

    @mcp.resource("andre://references/insee_variables")
    def insee_variables() -> str:
        """Variables socio-démographiques INSEE (RP 2022 + Filosofi 2021)."""
        path = refs_dir / "insee_variables.md"
        return path.read_text(encoding="utf-8") if path.exists() else "Fichier introuvable."

    @mcp.resource("andre://skills/electoral-analysis")
    def skill_electoral() -> str:
        """Skill principal : workflows, debugging, bonnes pratiques."""
        path = skills_dir / "SKILL.md"
        return path.read_text(encoding="utf-8") if path.exists() else "Fichier introuvable."

    @mcp.resource("andre://references/sql_schema")
    def sql_schema() -> str:
        """Schéma SQL DuckDB — tables, vues et colonnes pour les requêtes query_sql.

        IMPORTANT : lire cette resource avant d'écrire des requêtes SQL directes.
        Les noms de colonnes contiennent des espaces et accents (à quoter avec "").
        """
        return """# Schéma SQL — André DuckDB

## ATTENTION : conventions de nommage

- Les colonnes des Parquet officiels ont des **noms avec espaces et accents** : `"Code de la commune"`, `"Libellé du département"`, `Inscrits`, `Abstentions`, etc.
- Le **code commune est SANS préfixe département** : Marseille = `"Code du département" = '13' AND "Code de la commune" = '055'` (pas '13055').
- Pour requêter par code commune complet (13055), utiliser `query_elections` (pas `query_sql`).
- Les pourcentages pré-calculés (`pct_abstention`, `pct_participation`) existent dans la vue `general_results`.

## Tables et vues principales

### general_results (vue)
Résultats généraux par bureau de vote, toutes élections 1999-2026.
| Colonne | Type | Description |
|---------|------|-------------|
| id_election | VARCHAR | Ex: '2024_legi_t1', '2020_muni_t2' |
| id_brut_miom | VARCHAR | Identifiant unique du bureau de vote |
| "Code du département" | VARCHAR | Code dept (ex: '13', '2A') |
| "Code de la commune" | VARCHAR | Code commune SANS dept (ex: '055') |
| "Libellé de la commune" | VARCHAR | Nom commune |
| Inscrits | BIGINT | Nombre d'inscrits |
| Abstentions | BIGINT | Nombre d'abstentions |
| Votants | BIGINT | Nombre de votants |
| Blancs | BIGINT | Bulletins blancs |
| Nuls | BIGINT | Bulletins nuls |
| Exprimés | BIGINT | Suffrages exprimés |
| pct_abstention | DOUBLE | 100 × Abstentions / Inscrits |
| pct_participation | DOUBLE | 100 × Votants / Inscrits |

### candidats_results (vue)
Résultats par candidat/liste par bureau de vote.
| Colonne | Type | Description |
|---------|------|-------------|
| id_election | VARCHAR | Identifiant élection |
| id_brut_miom | VARCHAR | Bureau de vote |
| Nom | VARCHAR | Nom du candidat |
| Prénom | VARCHAR | Prénom du candidat |
| Nuance | VARCHAR | Code nuance politique (ex: 'RN', 'LFI') |
| Voix | BIGINT | Nombre de voix |
| pct_voix_inscrits | DOUBLE | % des inscrits |
| pct_voix_exprimes | DOUBLE | % des exprimés |

### candidats_avec_nuances (vue)
Comme candidats_results + colonne `famille_politique` (Gauche, Centre, Droite, Extrême droite, Autre).

### resultats_par_famille (vue)
Résultats agrégés par famille politique par bureau.
| Colonne | Type |
|---------|------|
| id_election | VARCHAR |
| id_brut_miom | VARCHAR |
| famille_politique | VARCHAR |
| total_voix | BIGINT |

### elections_disponibles (vue)
| Colonne | Type | Description |
|---------|------|-------------|
| id_election | VARCHAR | Identifiant unique |
| nb_bureaux | BIGINT | Nombre de bureaux de vote |
| total_inscrits | HUGEINT | Total national des inscrits |

### communes (table)
| Colonne | Type | Description |
|---------|------|-------------|
| code | VARCHAR | Code INSEE complet (ex: '13055') |
| nom | VARCHAR | Nom de la commune |
| departement_code | VARCHAR | Code département |
| population | DOUBLE | Population |

### bv_sociodemographique (vue)
Données INSEE RP 2022 + Filosofi 2021 interpolées au niveau bureau de vote.
Colonnes clés : `code_commune` (complet !), `pct_cadres`, `pct_ouvriers`, `taux_chomage`,
`indice_precarite`, `indice_vieillissement`, `MED21` (revenu médian), etc.

## Exemples de requêtes

```sql
-- Abstention moyenne à Marseille par élection
SELECT id_election, ROUND(AVG(pct_abstention), 1) as abstention
FROM general_results
WHERE "Code du département" = '13' AND "Code de la commune" = '055'
GROUP BY id_election ORDER BY id_election DESC LIMIT 5

-- Top 5 candidats nationaux aux européennes 2024
SELECT Nom, Prénom, Nuance, SUM(Voix) as total_voix
FROM candidats_results WHERE id_election = '2024_euro_t1'
GROUP BY Nom, Prénom, Nuance ORDER BY total_voix DESC LIMIT 5

-- Corrélation CSP-cadres et vote par commune (13)
SELECT c.nom, s.pct_cadres, r.total_voix
FROM bv_sociodemographique s
JOIN resultats_par_famille r ON s.id_brut_miom = r.id_brut_miom
JOIN communes c ON s.code_commune = c.code
WHERE r.id_election = '2024_legi_t1' AND r.famille_politique = 'Gauche'
  AND s.code_commune LIKE '13%'
```
"""


def _register_prompts(mcp: FastMCP) -> None:
    """Enregistre les prompts MCP pré-construits."""

    @mcp.prompt()
    def analyser_commune(commune: str) -> str:
        """Analyse électorale complète d'une commune."""
        return (
            f"Réalise une analyse électorale complète de {commune} :\n"
            "1. Recherche le code commune via query_admin\n"
            "2. Liste les élections disponibles\n"
            "3. Analyse les résultats des 3 dernières élections (par famille politique)\n"
            "4. Compare la participation dans le temps\n"
            "5. Identifie les tendances politiques dominantes\n"
            "6. Si disponible, croise avec les données socio-démographiques (query_sociodem)\n"
            "7. Conclus avec une synthèse des dynamiques électorales"
        )

    @mcp.prompt()
    def reserves_de_voix(election: str, commune: str, force_politique: str) -> str:
        """Identification des réserves de voix pour une force politique."""
        return (
            f"Identifie les réserves de voix pour {force_politique} "
            f"dans la commune de {commune} lors de l'élection {election} :\n"
            "1. Requête les résultats par bureau de vote (query_by_bureau)\n"
            "2. Identifie les bureaux avec forte abstention ET potentiel socio-démo favorable\n"
            "3. Compare avec les scores historiques dans ces bureaux\n"
            "4. Croise avec le profil socio-démographique (query_sociodem)\n"
            "5. Quantifie le potentiel de mobilisation"
        )

    @mcp.prompt()
    def carte_electorale(election: str, departement: str) -> str:
        """Génération d'une carte électorale thématique."""
        return (
            f"Génère une analyse cartographique de l'élection {election} "
            f"dans le département {departement}.\n\n"
            "WORKFLOW — utilise les outils MCP dans cet ordre :\n\n"
            "1. **query_elections** (action='query_results', election_id='{election}', "
            f"departement_code='{departement}', by_family=true)\n"
            "   → Vérifie que l'élection existe et récupère les résultats agrégés\n\n"
            f"2. **generate_map** (election_id='{election}', department='{departement}', "
            "metric='family')\n"
            "   → Génère la carte par famille politique en tête\n\n"
            "3. **query_elections** (action='rank_bureaux', election_id='{election}', "
            f"commune_code ou departement_code='{departement}', rank_metric='participation')\n"
            "   → Identifie les bureaux les plus/moins participatifs\n\n"
            "4. Analyse les résultats : zones de force par camp, participation, "
            "contrastes territoriaux\n\n"
            "PARAMÈTRES generate_map :\n"
            "- metric='family' : carte de la famille politique en tête par bureau\n"
            "- metric='nuance', nuance='RN' : carte du score d'une nuance spécifique\n"
            "- geography='bv' (défaut) ou 'commune' (carte nationale)"
        )

    @mcp.prompt()
    def comparer_elections(election1: str, election2: str, territoire: str) -> str:
        """Comparaison temporelle entre deux élections."""
        return (
            f"Compare les élections {election1} et {election2} "
            f"sur le territoire {territoire} :\n"
            "1. Évolution de la participation\n"
            "2. Transferts de voix entre familles politiques\n"
            "3. Dynamiques territoriales (quels bureaux basculent ?)\n"
            "4. Facteurs explicatifs possibles"
        )

    @mcp.prompt()
    def resultats_rapides(election: str) -> str:
        """Résultats synthétiques d'une élection."""
        return (
            f"Donne les résultats synthétiques de l'élection {election} :\n"
            "1. Résultats nationaux par famille politique\n"
            "2. Participation nationale\n"
            "3. Top 5 des candidats/listes\n"
            "4. Comparaison avec l'élection précédente du même type"
        )


def _register_landing_page(mcp: FastMCP) -> None:
    """Ajoute une page d'accueil HTML sur GET /."""
    from starlette.requests import Request
    from starlette.responses import HTMLResponse

    @mcp.custom_route("/", methods=["GET"])
    async def landing_page(request: Request) -> HTMLResponse:
        return HTMLResponse(LANDING_HTML)


def _register_metrics_route(mcp: FastMCP) -> None:
    """Expose les métriques Prometheus sur GET /metrics."""

    @mcp.custom_route("/metrics", methods=["GET"])
    async def metrics(_request: Any) -> Response:
        return Response(
            content=generate_latest(),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )


LANDING_HTML = """\
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>André MCP — Analyse électorale française</title>
    <link rel="icon" href="https://andre.vote/img/andre.png">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: linear-gradient(to bottom, #f9fafb, #ffffff);
            color: #171717;
            line-height: 1.6;
            min-height: 100vh;
        }
        header {
            background: rgba(255,255,255,0.8);
            backdrop-filter: blur(8px);
            border-bottom: 1px solid #e5e7eb;
            position: sticky; top: 0; z-index: 50;
        }
        .header-inner {
            max-width: 720px; margin: 0 auto; padding: 0.75rem 1.5rem;
            display: flex; align-items: center; gap: 0.75rem;
        }
        .avatar {
            width: 36px; height: 36px; border-radius: 50%; border: 1px solid #e5e7eb;
            object-fit: cover;
        }
        .header-title { font-weight: 600; font-size: 1rem; color: #111827; }
        .header-sub { font-size: 0.75rem; color: #6b7280; }
        .container { max-width: 720px; margin: 0 auto; padding: 2.5rem 1.5rem 3rem; }

        /* Hero */
        .hero { text-align: center; margin-bottom: 2.5rem; }
        .badge-line { margin-bottom: 1rem; }
        .badge {
            display: inline-flex; align-items: center; gap: 0.4rem;
            background: #dbeafe; color: #1e40af;
            font-size: 0.8rem; font-weight: 500;
            padding: 0.25rem 0.75rem; border-radius: 9999px;
        }
        .badge-dot {
            width: 6px; height: 6px; border-radius: 50%; background: #3b82f6;
            animation: pulse 2s infinite;
        }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
        h1 { font-size: 2.2rem; font-weight: 700; color: #111827; line-height: 1.2; margin-bottom: 0.75rem; }
        h1 span { color: #2563eb; }
        .hero p { color: #4b5563; font-size: 1.05rem; max-width: 540px; margin: 0 auto; }

        /* Cards */
        .card {
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1rem;
            transition: border-color 0.15s, box-shadow 0.15s;
        }
        .card:hover { border-color: #93c5fd; box-shadow: 0 4px 12px rgba(37,99,235,0.08); }
        .card h2 { font-size: 1rem; font-weight: 600; color: #111827; margin-bottom: 0.75rem; }

        /* Steps */
        .step { display: flex; gap: 0.75rem; margin-bottom: 0.75rem; }
        .step-num {
            flex-shrink: 0; width: 26px; height: 26px;
            background: #2563eb; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 0.75rem; font-weight: 700; color: white; margin-top: 2px;
        }
        .step-content { flex: 1; }
        .step-content p { color: #4b5563; font-size: 0.9rem; }

        /* Code blocks */
        pre {
            background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 8px;
            padding: 0.75rem 1rem; overflow-x: auto;
            font-size: 0.82rem; line-height: 1.5; margin-top: 0.4rem;
        }
        code { font-family: 'JetBrains Mono', 'SF Mono', 'Fira Code', ui-monospace, monospace; color: #1f2937; }
        .path-label { color: #9ca3af; font-size: 0.8rem; }
        .copy-btn {
            float: right; background: #e5e7eb; border: none; color: #6b7280;
            padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 0.72rem;
        }
        .copy-btn:hover { background: #2563eb; color: white; }

        /* Tools grid */
        .tools-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.4rem; }
        .tool {
            background: #f9fafb; border: 1px solid #f3f4f6; border-radius: 8px;
            padding: 0.5rem 0.7rem;
        }
        .tool code { color: #2563eb; font-size: 0.78rem; }
        .tool span { color: #6b7280; font-size: 0.78rem; display: block; margin-top: 1px; }

        /* Sources */
        .sources { color: #6b7280; font-size: 0.85rem; }
        .sources a { color: #2563eb; text-decoration: none; }
        .sources a:hover { text-decoration: underline; }

        /* Footer */
        .page-footer {
            text-align: center; color: #9ca3af; font-size: 0.82rem;
            margin-top: 2rem; padding-top: 1.5rem; border-top: 1px solid #e5e7eb;
        }
        .page-footer a { color: #2563eb; text-decoration: none; }
        .page-footer a:hover { text-decoration: underline; }

        /* CTA */
        .cta {
            display: inline-flex; align-items: center; gap: 0.4rem;
            background: #2563eb; color: white; font-weight: 600; font-size: 0.9rem;
            padding: 0.6rem 1.5rem; border-radius: 8px; text-decoration: none;
            box-shadow: 0 4px 14px rgba(37,99,235,0.25); transition: background 0.15s;
        }
        .cta:hover { background: #1d4ed8; }

        @media (max-width: 600px) {
            .tools-grid { grid-template-columns: 1fr; }
            h1 { font-size: 1.6rem; }
        }
    </style>
</head>
<body>
    <header>
        <div class="header-inner">
            <img src="https://andre.vote/img/andre.png" alt="" class="avatar">
            <div>
                <div class="header-title">André</div>
                <div class="header-sub">Analyseur Electoral</div>
            </div>
        </div>
    </header>

    <div class="container">
        <div class="hero">
            <div class="badge-line">
                <span class="badge"><span class="badge-dot"></span> Données 1999-2026</span>
            </div>
            <h1>L'analyse électorale<br><span>dans votre Claude</span></h1>
            <p>
                Connectez André à Claude Desktop ou Claude Code pour analyser
                55 élections françaises au niveau bureau de vote.
            </p>
        </div>

        <div class="card">
            <h2>Connexion depuis Claude Desktop</h2>
            <div class="step">
                <div class="step-num">1</div>
                <div class="step-content">
                    <p>Ouvrez <strong>claude_desktop_config.json</strong> :</p>
                    <pre><code><span class="path-label">macOS</span>    ~/Library/Application Support/Claude/claude_desktop_config.json
<span class="path-label">Windows</span>  %APPDATA%\\Claude\\claude_desktop_config.json
<span class="path-label">Linux</span>    ~/.config/Claude/claude_desktop_config.json</code></pre>
                </div>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <div class="step-content">
                    <p>Ajoutez dans <strong>mcpServers</strong> :</p>
                    <pre><button class="copy-btn" onclick="copyConfig('desktop')">copier</button><code id="config-desktop">"andre": {
  "command": "npx",
  "args": ["-y", "mcp-remote@latest",
    "https://mcp.andre.vote/mcp"]
}</code></pre>
                </div>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <div class="step-content">
                    <p>Relancez Claude Desktop. André apparaît dans vos outils MCP.</p>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>Connexion depuis Claude Code</h2>
            <pre><button class="copy-btn" onclick="copyConfig('code')">copier</button><code id="config-code">claude mcp add andre-mcp -- npx -y mcp-remote@latest https://mcp.andre.vote/mcp</code></pre>
        </div>

        <div class="card">
            <h2>11 outils disponibles</h2>
            <div class="tools-grid">
                <div class="tool"><code>query_elections</code><span>Résultats électoraux</span></div>
                <div class="tool"><code>query_admin</code><span>Divisions administratives</span></div>
                <div class="tool"><code>query_sociodem</code><span>Données INSEE par BDV</span></div>
                <div class="tool"><code>query_elus</code><span>Élus (base BRÉF)</span></div>
                <div class="tool"><code>query_sql</code><span>SQL direct (DuckDB)</span></div>
                <div class="tool"><code>generate_map</code><span>Cartes électorales</span></div>
                <div class="tool"><code>visualize</code><span>Graphiques Plotly</span></div>
                <div class="tool"><code>query_dlp</code><span>Profil social candidats</span></div>
                <div class="tool"><code>simulate_fusions</code><span>Fusions de listes</span></div>
                <div class="tool"><code>export_data</code><span>Export CSV/JSON</span></div>
                <div class="tool"><code>create_data_table</code><span>Tableaux structurés</span></div>
            </div>
        </div>

        <div class="card">
            <h2>5 prompts pré-construits</h2>
            <div class="tools-grid">
                <div class="tool"><code>analyser_commune</code><span>Analyse complète</span></div>
                <div class="tool"><code>reserves_de_voix</code><span>Potentiel électoral</span></div>
                <div class="tool"><code>carte_electorale</code><span>Carte thématique</span></div>
                <div class="tool"><code>comparer_elections</code><span>Évolution temporelle</span></div>
                <div class="tool"><code>resultats_rapides</code><span>Synthèse rapide</span></div>
            </div>
        </div>

        <div class="card">
            <h2>Données</h2>
            <p class="sources">
                55 élections (1999-2026) au niveau bureau de vote. Sources :
                <a href="https://data.gouv.fr">data.gouv.fr</a>,
                INSEE, IGN, BRÉF (Zenodo), FERD.
                Licence Ouverte 2.0 / CC BY 4.0.
            </p>
        </div>

        <div class="card" style="border-color: #fbbf24; background: #fffbeb;">
            <h2 style="color: #92400e;">&#9888;&#65039; Limite d'appels d'outils</h2>
            <p style="color: #78350f; font-size: 0.9rem;">
                Claude impose une limite d'environ 15-20 appels d'outils par tour de conversation.
                Les analyses électorales approfondies (résultats T1+T2, bureaux de vote,
                croisement socio-démo…) peuvent atteindre cette limite.
                Quand le message « Claude a atteint sa limite d'utilisation d'outils » apparaît,
                cliquez <strong>Continuer</strong> pour reprendre l'analyse.
            </p>
            <p style="color: #78350f; font-size: 0.9rem; margin-top: 0.5rem;">
                Pour des analyses complètes sans interruption, utilisez
                <a href="https://andre.vote" style="color: #2563eb; font-weight: 600;">andre.vote</a>
                — l'interface web dédiée qui n'a pas cette contrainte.
            </p>
        </div>

        <div style="text-align:center; margin: 2rem 0;">
            <a href="https://andre.vote" class="cta">Essayer André sur le web &rarr;</a>
        </div>

        <div class="page-footer">
            <a href="https://github.com/joelgombin/andre">GitHub</a>
            &nbsp;&middot;&nbsp;
            Propulsé par <a href="https://modelcontextprotocol.io">Model Context Protocol</a>
            &nbsp;&middot;&nbsp;
            Développé par <strong>Joël Gombin</strong>
        </div>
    </div>

    <!-- Matomo -->
    <script>
      var _paq = window._paq = window._paq || [];
      _paq.push(['trackPageView']);
      _paq.push(['enableLinkTracking']);
      (function() {
        var u="//matomo.apps.joelgombin.fr/";
        _paq.push(['setTrackerUrl', u+'matomo.php']);
        _paq.push(['setSiteId', '4']);
        var d=document, g=d.createElement('script'), s=d.getElementsByTagName('script')[0];
        g.async=true; g.src=u+'matomo.js'; s.parentNode.insertBefore(g,s);
      })();
    </script>

    <script>
    function copyConfig(type) {
        const el = document.getElementById('config-' + type);
        navigator.clipboard.writeText(el.textContent);
        const btn = el.parentElement.querySelector('.copy-btn');
        btn.textContent = 'copié !';
        setTimeout(() => btn.textContent = 'copier', 1500);
    }
    </script>
</body>
</html>
"""


# Point d'entrée pour stdio
def run():
    """Lance le serveur MCP en mode stdio."""
    server = create_server()
    server.run(transport="stdio")


def run_http(host: str = "0.0.0.0", port: int = 8080):
    """Lance le serveur MCP en mode Streamable HTTP.

    Les utilisateurs se connectent via :
    {"url": "http://<host>:<port>/mcp"}

    Args:
        host: Adresse d'écoute (0.0.0.0 pour toutes les interfaces).
        port: Port d'écoute.
    """
    import os

    from mcp.server.transport_security import TransportSecuritySettings

    server = create_server()
    # Configurer host/port via les settings FastMCP
    server.settings.host = host
    server.settings.port = port

    # Désactiver la protection DNS rebinding pour le déploiement derrière un reverse proxy.
    # CapRover/nginx forward le header Host avec le domaine public, qui n'est pas
    # dans la liste des hosts autorisés par défaut (localhost uniquement).
    # En production derrière un reverse proxy, la protection est assurée par nginx.
    allowed_hosts = os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
    allowed_hosts = [h.strip() for h in allowed_hosts if h.strip()]

    if allowed_hosts:
        server.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        )
    else:
        # Pas de hosts configurés → désactiver la protection
        # (le reverse proxy s'en charge)
        server.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )

    server.run(transport="streamable-http")
