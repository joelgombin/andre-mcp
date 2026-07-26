"""CLI de téléchargement des données et initialisation DuckDB pour andre-mcp."""

from pathlib import Path

import requests
from tqdm import tqdm

# ==========================================
# CATALOGUE DES JEUX DE DONNÉES
# ==========================================

DATASETS = {
    # --- Essentiels (category=essential, ~280 Mo) ---
    "general_results": {
        "url": "https://object.files.data.gouv.fr/data-pipeline-open/elections/general_results.parquet",
        "output": "resultats/general_results.parquet",
        "category": "essential",
        "description": "Résultats généraux bureau de vote (1999-2024, ~136 Mo)",
    },
    "candidats_results": {
        "url": "https://object.files.data.gouv.fr/data-pipeline-open/elections/candidats_results.parquet",
        "output": "resultats/candidats_results.parquet",
        "category": "essential",
        "description": "Résultats par candidat bureau de vote (1999-2024, ~87 Mo)",
    },
    "nuances": {
        "url": "https://www.data.gouv.fr/api/1/datasets/r/6fd17a6c-519b-465c-a7fd-ad2955fafc76",
        "output": "resultats/nuances.csv",
        "category": "essential",
        "description": "Codes de nuance politique (<1 Mo)",
    },
    "table_bv_reu": {
        "url": "https://static.data.gouv.fr/resources/bureaux-de-vote-et-adresses-de-leurs-electeurs/20230626-135809/table-bv-reu.parquet",
        "output": "resultats/table_bv_reu.parquet",
        "category": "essential",
        "description": "Table de correspondance BV REU-MIOM (~50 Mo)",
    },
    "circ_legi_2024": {
        "url": "https://static.data.gouv.fr/resources/elections-legislatives-des-30-juin-et-7-juillet-2024-resultats-definitifs-du-1er-tour/20240710-171413/resultats-definitifs-par-circonscriptions-legislatives.csv",
        "output": "resultats/2024_legi_t1_par_circ.csv",
        "category": "essential",
        "description": "Mapping BV→circonscriptions législatives 2024 (<5 Mo)",
    },
    "communes": {
        "url": "https://unpkg.com/@etalab/decoupage-administratif@4.1.0/data/communes.json",
        "output": "administratif/communes.json",
        "category": "essential",
        "description": "Communes (JSON @etalab, ~8 Mo)",
        "convert": "communes",
    },
    "epci": {
        "url": "https://unpkg.com/@etalab/decoupage-administratif@4.1.0/data/epci.json",
        "output": "administratif/epci.json",
        "category": "essential",
        "description": "EPCI (JSON @etalab, ~1 Mo)",
        "convert": "epci",
    },
    "departements": {
        "url": "https://unpkg.com/@etalab/decoupage-administratif@4.1.0/data/departements.json",
        "output": "administratif/departements.json",
        "category": "essential",
        "description": "Départements (JSON @etalab, <1 Mo)",
        "convert": "departements",
    },
    "regions": {
        "url": "https://unpkg.com/@etalab/decoupage-administratif@4.1.0/data/regions.json",
        "output": "administratif/regions.json",
        "category": "essential",
        "description": "Régions (JSON @etalab, <1 Mo)",
        "convert": "regions",
    },
    # --- Cartes (category=maps, ~60 Mo) ---
    "communes_parquet": {
        "url": "https://static.data.gouv.fr/resources/communes-et-epci-2025-admin-express-cog-plus-ign/20250624-143940/a-com2025.parquet",
        "output": "contours/communes-national.parquet",
        "category": "maps",
        "description": "Contours communes GeoParquet national (~13 Mo, icem7)",
    },
    "communes_topo": {
        "url": "https://static.data.gouv.fr/resources/communes-et-epci-2025-admin-express-cog-plus-ign/20250624-144024/a-com2025-topo.json",
        "output": "contours/communes-national.topojson",
        "category": "maps",
        "description": "Contours communes TopoJSON national (~12 Mo, icem7)",
    },
    # --- FERD (category=ferd, ~3 Mo) ---
    # Note: FERD nécessite un accès CDSP — les métadonnées sont accessibles mais pas les données
    # Les utilisateurs qui ont les données FERD peuvent les placer manuellement dans ferd/parquet/
}


def download_file(
    url: str,
    output_path: Path,
    force: bool = False,
    description: str = "",
) -> bool:
    """Télécharge un fichier avec barre de progression.

    Args:
        url: URL du fichier à télécharger.
        output_path: Chemin de sortie local.
        force: Si True, retélécharge même si le fichier existe.
        description: Description pour la barre de progression.

    Returns:
        True si le fichier a été téléchargé, False si déjà présent.
    """
    if output_path.exists() and not force:
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"  ○ {description or output_path.name} ({size_mb:.1f} Mo) — déjà présent")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"  ✗ Erreur téléchargement {output_path.name}: {e}")
        return False

    total = int(response.headers.get("content-length", 0))
    desc = description or output_path.name

    with open(output_path, "wb") as f:
        with tqdm(total=total, unit="B", unit_scale=True, desc=f"  ↓ {desc}") as pbar:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                pbar.update(len(chunk))

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  ✓ {desc} ({size_mb:.1f} Mo)")
    return True


def convert_admin_json(data_dir: Path, dataset_name: str) -> None:
    """Convertit un fichier JSON admin en Parquet.

    Args:
        data_dir: Répertoire de données.
        dataset_name: Nom du dataset (communes, epci, departements, regions).
    """
    import pandas as pd

    json_path = data_dir / "administratif" / f"{dataset_name}.json"
    parquet_path = data_dir / "administratif" / f"{dataset_name}.parquet"

    if parquet_path.exists():
        return

    if not json_path.exists():
        return

    import json
    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)

    if dataset_name == "communes":
        rows = []
        for c in raw:
            rows.append({
                "code": c.get("code", ""),
                "nom": c.get("nom", ""),
                "departement": c.get("departement", ""),
                "region": c.get("region", ""),
                "population": c.get("population", 0),
                "codesPostaux": ",".join(c.get("codesPostaux", [])),
            })
        df = pd.DataFrame(rows)
    elif dataset_name == "epci":
        rows = []
        for e in raw:
            for membre in e.get("membres", []):
                rows.append({
                    "epci_code": e.get("code", ""),
                    "epci_nom": e.get("nom", ""),
                    "commune_code": membre.get("code", ""),
                })
        df = pd.DataFrame(rows)
        # Sauvegarder aussi le mapping commune→EPCI
        commune_epci_path = data_dir / "administratif" / "commune_epci.parquet"
        df.to_parquet(commune_epci_path, index=False)

        # EPCI principal
        epci_rows = []
        for e in raw:
            epci_rows.append({
                "code": e.get("code", ""),
                "nom": e.get("nom", ""),
                "type": e.get("type", ""),
                "nb_membres": len(e.get("membres", [])),
            })
        df = pd.DataFrame(epci_rows)
    elif dataset_name == "departements":
        df = pd.DataFrame([{
            "code": d.get("code", ""),
            "nom": d.get("nom", ""),
            "region": d.get("region", ""),
        } for d in raw])
    elif dataset_name == "regions":
        df = pd.DataFrame([{
            "code": r.get("code", ""),
            "nom": r.get("nom", ""),
        } for r in raw])
    else:
        return

    df.to_parquet(parquet_path, index=False)
    print(f"  ✓ Converti {dataset_name}.json → {dataset_name}.parquet ({len(df)} lignes)")


def check_dataset_status(data_dir: Path) -> dict:
    """Vérifie l'état des données par catégorie.

    Args:
        data_dir: Répertoire de données.

    Returns:
        Dict {category: {total, downloaded, size_mb, files: [{name, exists, size_mb}]}}
    """
    categories: dict[str, dict] = {}

    for name, ds in DATASETS.items():
        cat = ds["category"]
        if cat not in categories:
            categories[cat] = {"total": 0, "downloaded": 0, "size_mb": 0.0, "files": []}

        output_path = data_dir / ds["output"]
        exists = output_path.exists()
        size_mb = output_path.stat().st_size / (1024 * 1024) if exists else 0.0

        categories[cat]["total"] += 1
        if exists:
            categories[cat]["downloaded"] += 1
            categories[cat]["size_mb"] += size_mb

        categories[cat]["files"].append({
            "name": name,
            "exists": exists,
            "size_mb": round(size_mb, 1),
            "description": ds.get("description", ""),
        })

    return categories


def download_datasets(
    data_dir: Path,
    categories: list[str] | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Télécharge les datasets par catégorie.

    Args:
        data_dir: Répertoire de destination.
        categories: Liste de catégories à télécharger. None = essential seulement.
        force: Retélécharger même si existant.

    Returns:
        Dict {downloaded: int, skipped: int, failed: int}
    """
    if categories is None:
        categories = ["essential"]

    stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    for name, ds in DATASETS.items():
        if ds["category"] not in categories:
            continue

        output_path = data_dir / ds["output"]
        result = download_file(
            url=ds["url"],
            output_path=output_path,
            force=force,
            description=ds.get("description", name),
        )

        if result:
            stats["downloaded"] += 1
            # Convertir les JSON admin en Parquet si nécessaire
            if "convert" in ds:
                convert_admin_json(data_dir, ds["convert"])
        elif output_path.exists():
            stats["skipped"] += 1
        else:
            stats["failed"] += 1

    return stats


def build_duckdb(data_dir: Path, db_path: Path | None = None) -> bool:
    """Construit la base DuckDB à partir des fichiers Parquet.

    Crée les vues essentielles pour que les query scripts fonctionnent.

    Args:
        data_dir: Répertoire contenant les fichiers Parquet.
        db_path: Chemin de la base DuckDB. Par défaut: data_dir/elections.duckdb.

    Returns:
        True si la construction a réussi.
    """
    import duckdb

    if db_path is None:
        db_path = data_dir / "elections.duckdb"

    # Supprimer l'ancienne base si elle existe
    if db_path.exists():
        db_path.unlink()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))

    try:
        _create_views(con, data_dir)
        print(f"\n✓ Base DuckDB créée : {db_path} ({db_path.stat().st_size / 1024:.0f} Ko)")
        return True
    except Exception as e:
        print(f"\n✗ Erreur construction DuckDB : {e}")
        return False
    finally:
        con.close()


def _create_views(con, data_dir: Path) -> None:
    """Crée les vues DuckDB essentielles."""

    def rp(path: Path) -> str:
        """Chemin relatif ou absolu selon le contexte."""
        return str(path)

    # --- 1. Résultats généraux ---
    general_path = data_dir / "resultats" / "general_results.parquet"
    if general_path.exists():
        print("  ✓ Vue general_results")
        con.execute(f"""
            CREATE OR REPLACE VIEW _general_results_raw AS
            SELECT * FROM read_parquet('{rp(general_path)}')
        """)
        con.execute("""
            CREATE OR REPLACE VIEW general_results AS
            SELECT g.*,
                ROUND(100.0 * g.Abstentions / NULLIF(g.Inscrits, 0), 2) as pct_abstention,
                ROUND(100.0 * g.Votants / NULLIF(g.Inscrits, 0), 2) as pct_participation,
                ROUND(100.0 * g.Blancs / NULLIF(g.Inscrits, 0), 2) as pct_blancs_inscrits,
                ROUND(100.0 * g.Nuls / NULLIF(g.Inscrits, 0), 2) as pct_nuls_inscrits,
                ROUND(100.0 * g.Exprimés / NULLIF(g.Inscrits, 0), 2) as pct_exprimes_inscrits
            FROM _general_results_raw g
        """)

    # --- 2. Résultats candidats ---
    candidats_path = data_dir / "resultats" / "candidats_results.parquet"
    if candidats_path.exists() and general_path.exists():
        print("  ✓ Vue candidats_results")
        con.execute(f"""
            CREATE OR REPLACE VIEW candidats_results AS
            SELECT c.*,
                ROUND(100.0 * c.Voix / NULLIF(g.Inscrits, 0), 2) as pct_voix_inscrits,
                ROUND(100.0 * c.Voix / NULLIF(g.Exprimés, 0), 2) as pct_voix_exprimes
            FROM read_parquet('{rp(candidats_path)}') c
            LEFT JOIN (
                SELECT id_election, id_brut_miom, Inscrits, Exprimés
                FROM read_parquet('{rp(general_path)}')
            ) g ON c.id_brut_miom = g.id_brut_miom AND c.id_election = g.id_election
        """)

    # --- 3. Nuances ---
    nuances_enriched = data_dir / "resultats" / "nuances_enriched.parquet"
    nuances_csv = data_dir / "resultats" / "nuances.csv"
    if nuances_enriched.exists():
        print("  ✓ Table nuances (enrichie)")
        con.execute(f"CREATE OR REPLACE TABLE nuances AS SELECT * FROM read_parquet('{rp(nuances_enriched)}')")
    elif nuances_csv.exists():
        print("  ✓ Table nuances (brute)")
        con.execute(f"CREATE OR REPLACE TABLE nuances AS SELECT * FROM read_csv_auto('{rp(nuances_csv)}')")

    # --- 4. Table BV REU ---
    reu_path = data_dir / "resultats" / "table_bv_reu.parquet"
    if reu_path.exists():
        print("  ✓ Table table_bv_reu")
        con.execute(f"CREATE OR REPLACE TABLE table_bv_reu AS SELECT * FROM read_parquet('{rp(reu_path)}')")

    # --- 5. Divisions administratives ---
    for table_name in ["communes", "departements", "regions"]:
        path = data_dir / "administratif" / f"{table_name}.parquet"
        if path.exists():
            print(f"  ✓ Table {table_name}")
            con.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_parquet('{rp(path)}')")

    epci_path = data_dir / "administratif" / "epci.parquet"
    if epci_path.exists():
        print("  ✓ Table epci")
        con.execute(f"CREATE OR REPLACE TABLE epci AS SELECT * FROM read_parquet('{rp(epci_path)}')")

    commune_epci_path = data_dir / "administratif" / "commune_epci.parquet"
    if commune_epci_path.exists():
        print("  ✓ Table commune_epci")
        con.execute(f"CREATE OR REPLACE TABLE commune_epci AS SELECT * FROM read_parquet('{rp(commune_epci_path)}')")

    # --- 6. FERD (optionnel) ---
    ferd_path = data_dir / "ferd" / "parquet" / "ferd_candidatures.parquet"
    if ferd_path.exists():
        print("  ✓ Table ferd_candidatures")
        con.execute(f"CREATE OR REPLACE TABLE ferd_candidatures AS SELECT * FROM read_parquet('{rp(ferd_path)}')")

    # --- 7. Candidatures 2026 (optionnel) ---
    for tour in (1, 2):
        cand_path = data_dir / "candidatures" / f"candidatures_2026_muni_t{tour}.parquet"
        if cand_path.exists():
            print(f"  ✓ Table candidatures_2026_muni_t{tour}")
            con.execute(f"""
                CREATE OR REPLACE TABLE candidatures_2026_muni_t{tour} AS
                SELECT * FROM read_parquet('{rp(cand_path)}')
            """)

    # --- 8. Vue élections disponibles ---
    print("  ✓ Vue elections_disponibles")
    con.execute("""
        CREATE OR REPLACE VIEW elections_disponibles AS
        SELECT DISTINCT
            id_election,
            MIN(CASE WHEN "Code de la commune" IS NOT NULL THEN "Code de la commune" END) as exemple_commune,
            COUNT(DISTINCT id_brut_miom) as nb_bureaux,
            SUM(Inscrits) as total_inscrits,
            SUM(Votants) as total_votants
        FROM general_results
        GROUP BY id_election
        ORDER BY id_election
    """)

    # --- 9. Vues par famille politique (si nuances disponibles) ---
    try:
        has_nuances = con.execute("SELECT COUNT(*) FROM nuances").fetchone()[0] > 0
    except Exception:
        has_nuances = False

    if has_nuances:
        try:
            # Déterminer les noms de colonnes (varient selon la source)
            nuance_cols = [row[0] for row in con.execute("DESCRIBE nuances").fetchall()]
            cand_cols = [row[0] for row in con.execute("DESCRIBE candidats_results").fetchall()]

            # Colonnes de jointure : "Nuance" (brut) ou "code_nuance" (enrichi)
            nuance_key = "Nuance" if "Nuance" in nuance_cols else "code_nuance"
            cand_nuance_key = "Nuance" if "Nuance" in cand_cols else "code_nuance"
            # Libellé nuance
            libelle_col = "Libellé" if "Libellé" in nuance_cols else "libelle_nuance"
            # Code commune
            commune_col = '"Code de la commune"' if "Code de la commune" in cand_cols else "code_commune"

            if "famille_politique" in nuance_cols:
                print("  ✓ Vue candidats_avec_nuances")
                con.execute(f"""
                    CREATE OR REPLACE VIEW candidats_avec_nuances AS
                    SELECT c.*, n."{libelle_col}" as libelle_nuance, n.famille_politique
                    FROM candidats_results c
                    LEFT JOIN nuances n ON c."{cand_nuance_key}" = n."{nuance_key}"
                """)

                print("  ✓ Vue resultats_par_famille")
                con.execute(f"""
                    CREATE OR REPLACE VIEW resultats_par_famille AS
                    SELECT
                        cn.id_election,
                        cn.id_brut_miom,
                        cn.{commune_col} as code_commune,
                        COALESCE(cn.famille_politique, 'Autre') as famille_politique,
                        SUM(cn.Voix) as voix,
                        SUM(cn.pct_voix_inscrits) as pct_inscrits,
                        SUM(cn.pct_voix_exprimes) as pct_exprimes
                    FROM candidats_avec_nuances cn
                    GROUP BY cn.id_election, cn.id_brut_miom, cn.{commune_col},
                             COALESCE(cn.famille_politique, 'Autre')
                """)
        except Exception as e:
            print(f"  ⚠ Vues par famille ignorées : {e}")

    # --- 10. Socio-démographique (optionnel) ---
    sociodem_path = data_dir / "sociodem" / "bv_sociodemographique.parquet"
    if sociodem_path.exists():
        print("  ✓ Vue bv_sociodemographique")
        con.execute(f"""
            CREATE OR REPLACE VIEW bv_sociodemographique AS
            SELECT * FROM read_parquet('{rp(sociodem_path)}')
        """)

    # --- 11. BRÉF (optionnel) ---
    bref_ind = data_dir / "bref" / "parquet" / "bref_individuals.parquet"
    bref_man = data_dir / "bref" / "parquet" / "bref_mandates.parquet"
    if bref_ind.exists() and bref_man.exists():
        print("  ✓ Tables bref_individuals + bref_mandates")
        con.execute(f"CREATE OR REPLACE TABLE bref_individuals AS SELECT * FROM read_parquet('{rp(bref_ind)}')")
        con.execute(f"CREATE OR REPLACE TABLE bref_mandates AS SELECT * FROM read_parquet('{rp(bref_man)}')")
