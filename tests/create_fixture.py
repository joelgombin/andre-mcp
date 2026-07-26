#!/usr/bin/env python3
"""
Crée un mini-dataset de test pour les CI.

Extrait quelques bureaux de vote de Marseille (2 élections) + les tables admin
et nuances, pour permettre aux tests d'intégration de tourner sans la DB complète.

Usage :
    uv run python tests/create_fixture.py

Crée : tests/fixtures/ avec les Parquet + un DuckDB prêt à l'emploi.
"""

import sys
from pathlib import Path

# Le script doit être lancé depuis la racine du projet
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FIXTURE_DIR = Path(__file__).parent / "fixtures"
ELECTIONS = ["2024_legi_t1", "2020_muni_t2"]
# 10 BDV de Marseille pour avoir des données légères
MAX_BDV = 10
DEPT = "13"
COMMUNE = "055"


def main():
    import duckdb
    import pandas as pd

    db_path = PROJECT_ROOT / "data" / "elections" / "elections.duckdb"
    if not db_path.exists():
        print(f"❌ DB introuvable : {db_path}")
        return 1

    con = duckdb.connect(str(db_path), read_only=True)
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    # --- 1. general_results (petit extrait) ---
    elections_filter = ", ".join(f"'{e}'" for e in ELECTIONS)
    gen_df = con.execute(f"""
        SELECT * FROM general_results
        WHERE id_election IN ({elections_filter})
          AND "Code du département" = '{DEPT}'
          AND "Code de la commune" = '{COMMUNE}'
        LIMIT {MAX_BDV * len(ELECTIONS)}
    """).df()
    (FIXTURE_DIR / "resultats").mkdir(exist_ok=True)
    gen_df.to_parquet(FIXTURE_DIR / "resultats" / "general_results.parquet", index=False)
    print(f"✓ general_results: {len(gen_df)} lignes")

    # --- 2. candidats_results ---
    bv_ids = gen_df["id_brut_miom"].unique().tolist()
    bv_filter = ", ".join(f"'{b}'" for b in bv_ids)
    cand_df = con.execute(f"""
        SELECT * FROM candidats_results
        WHERE id_election IN ({elections_filter})
          AND id_brut_miom IN ({bv_filter})
    """).df()
    cand_df.to_parquet(FIXTURE_DIR / "resultats" / "candidats_results.parquet", index=False)
    print(f"✓ candidats_results: {len(cand_df)} lignes")

    # --- 3. nuances ---
    nuances_df = con.execute("SELECT * FROM nuances").df()
    nuances_df.to_parquet(FIXTURE_DIR / "resultats" / "nuances_enriched.parquet", index=False)
    print(f"✓ nuances: {len(nuances_df)} lignes")

    # --- 4. communes (juste quelques-unes) ---
    (FIXTURE_DIR / "administratif").mkdir(exist_ok=True)
    communes_df = con.execute("""
        SELECT * FROM communes
        WHERE departement_code = '13'
        LIMIT 20
    """).df()
    communes_df.to_parquet(FIXTURE_DIR / "administratif" / "communes.parquet", index=False)
    print(f"✓ communes: {len(communes_df)} lignes")

    # --- 5. departements ---
    depts_df = con.execute("SELECT * FROM departements").df()
    depts_df.to_parquet(FIXTURE_DIR / "administratif" / "departements.parquet", index=False)
    print(f"✓ departements: {len(depts_df)} lignes")

    # --- 6. regions ---
    regions_df = con.execute("SELECT * FROM regions").df()
    regions_df.to_parquet(FIXTURE_DIR / "administratif" / "regions.parquet", index=False)
    print(f"✓ regions: {len(regions_df)} lignes")

    con.close()

    # --- 7. Construire le DuckDB fixture ---
    fixture_db = FIXTURE_DIR / "elections.duckdb"
    if fixture_db.exists():
        fixture_db.unlink()

    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from andre_mcp.setup_cli import build_duckdb

    success = build_duckdb(data_dir=FIXTURE_DIR, db_path=fixture_db)
    if success:
        size_kb = fixture_db.stat().st_size / 1024
        print(f"\n✅ Fixture créée : {FIXTURE_DIR} ({size_kb:.0f} Ko)")

        # Vérification
        con2 = duckdb.connect(str(fixture_db), read_only=True)
        n_elec = con2.execute("SELECT COUNT(*) FROM elections_disponibles").fetchone()[0]
        con2.close()
        print(f"   {n_elec} élections dans la fixture")
    else:
        print("\n❌ Échec construction DuckDB fixture")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
