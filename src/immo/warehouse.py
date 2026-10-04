import os
from pathlib import Path
from contextlib import contextmanager
from collections.abc import Iterator
import time
from threading import RLock

import duckdb


_DB_LOCK = RLock()


@contextmanager
def connect() -> Iterator[duckdb.DuckDBPyConnection]:
    _DB_LOCK.acquire()
    con = None
    try:
        db_path = os.environ["IMMO_DB"]
        if db_path != ":memory:":
            Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(20):
            try:
                con = duckdb.connect(db_path)
                break
            except duckdb.IOException as exc:
                if "Conflicting lock" not in str(exc) or attempt == 19:
                    raise
                time.sleep(0.25)
        assert con is not None
        _prepare(con, db_path)
        yield con
    finally:
        if con is not None:
            con.close()
        _DB_LOCK.release()


def _prepare(con: duckdb.DuckDBPyConnection, db_path: str) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS annonces_stg (
            source VARCHAR, external_id VARCHAR, categorie VARCHAR,
            type_bien VARCHAR, prix DOUBLE, loyer DOUBLE,
            surface_bati DOUBLE, surface_terrain DOUBLE,
            code_postal VARCHAR, ville VARCHAR, lat DOUBLE, lng DOUBLE,
            titre VARCHAR, url VARCHAR, collected_at TIMESTAMP,
            first_seen_at TIMESTAMP, last_seen_at TIMESTAMP, active BOOLEAN,
            description VARCHAR, nb_pieces INTEGER, nb_chambres INTEGER,
            dpe VARCHAR, ges VARCHAR, seller_type VARCHAR, seller_name VARCHAR,
            image_count INTEGER, published_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS prix_reference (
            code_postal VARCHAR, type_local VARCHAR, nb_ventes BIGINT,
            q1_eur_m2 DOUBLE, median_eur_m2 DOUBLE, q3_eur_m2 DOUBLE,
            premiere_vente DATE, derniere_vente DATE
        );
        CREATE TABLE IF NOT EXISTS loyer_reference (
            code_postal VARCHAR, median_loyer_m2_an DOUBLE
        );
        CREATE TABLE IF NOT EXISTS loyer_reference_commune (
            code_commune VARCHAR, code_postal VARCHAR, commune VARCHAR,
            type_bien VARCHAR, typologie VARCHAR,
            loyer_m2_mois DOUBLE, loyer_m2_bas DOUBLE, loyer_m2_haut DOUBLE,
            niveau_estimation VARCHAR, nb_observations INTEGER,
            r2 DOUBLE, millesime INTEGER, refreshed_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ventes_dvf (
            id_mutation VARCHAR, date_mutation DATE, code_postal VARCHAR,
            commune VARCHAR, adresse VARCHAR, type_local VARCHAR,
            surface_bati DOUBLE, valeur_fonciere DOUBLE, prix_m2 DOUBLE
        );
        CREATE TABLE IF NOT EXISTS collection_runs (
            run_id VARCHAR, source VARCHAR, search_url VARCHAR,
            started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR,
            rows_collected BIGINT, error VARCHAR
        );
        CREATE TABLE IF NOT EXISTS connector_url_checks (
            source VARCHAR, url VARCHAR, parser_version INTEGER,
            last_checked TIMESTAMP, status VARCHAR,
            PRIMARY KEY (source, url, parser_version)
        );
        CREATE TABLE IF NOT EXISTS annonce_reference (
            source VARCHAR, external_id VARCHAR, fingerprint UBIGINT,
            nb_ventes BIGINT, q1_eur_m2 DOUBLE, median_eur_m2 DOUBLE,
            q3_eur_m2 DOUBLE, premiere_vente DATE, derniere_vente DATE,
            surface_mediane_reference DOUBLE, score_comparabilite DOUBLE,
            refreshed_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS annonce_loyer_reference (
            source VARCHAR, external_id VARCHAR, fingerprint UBIGINT,
            loyer_m2_mois DOUBLE, loyer_m2_bas DOUBLE, loyer_m2_haut DOUBLE,
            niveau_estimation VARCHAR, nb_observations INTEGER,
            r2 DOUBLE, millesime INTEGER, refreshed_at TIMESTAMP
        );
    """)
    # Migrations légères pour les bases créées par les versions précédentes.
    for statement in (
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS active BOOLEAN",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS description VARCHAR",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS nb_pieces INTEGER",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS nb_chambres INTEGER",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS dpe VARCHAR",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS ges VARCHAR",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS seller_type VARCHAR",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS seller_name VARCHAR",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS image_count INTEGER",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS published_at TIMESTAMP",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS details_json VARCHAR",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS images_json VARCHAR",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS reference_annonce VARCHAR",
        # Ventes particulières : 'gre_a_gre' (défaut), 'enchere_judiciaire',
        # 'enchere_notariale', 'vente_interactive', 'cession_publique'.
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS mode_vente VARCHAR",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS date_vente TIMESTAMP",
        "ALTER TABLE annonces_stg ADD COLUMN IF NOT EXISTS prix_adjuge DOUBLE",
        "ALTER TABLE prix_reference ADD COLUMN IF NOT EXISTS premiere_vente DATE",
        "ALTER TABLE prix_reference ADD COLUMN IF NOT EXISTS derniere_vente DATE",
        "ALTER TABLE ventes_dvf ADD COLUMN IF NOT EXISTS code_departement VARCHAR",
        "ALTER TABLE ventes_dvf ADD COLUMN IF NOT EXISTS code_commune VARCHAR",
        "ALTER TABLE ventes_dvf ADD COLUMN IF NOT EXISTS nombre_pieces INTEGER",
        "ALTER TABLE ventes_dvf ADD COLUMN IF NOT EXISTS surface_terrain DOUBLE",
        "ALTER TABLE ventes_dvf ADD COLUMN IF NOT EXISTS longitude DOUBLE",
        "ALTER TABLE ventes_dvf ADD COLUMN IF NOT EXISTS latitude DOUBLE",
    ):
        con.execute(statement)
    # Le gros référentiel national vit dans un fichier séparé. Sa reconstruction
    # reste ainsi atomique et ne bloque pas l'interface pendant des heures.
    dvf_path = Path(os.getenv("DVF_DB", "data/dvf.duckdb")).expanduser().resolve()
    if db_path != ":memory:" and dvf_path.exists() and dvf_path != Path(db_path).expanduser().resolve():
        escaped = str(dvf_path).replace("'", "''")
        attached = con.execute(
            "SELECT count(*) FROM duckdb_databases() WHERE database_name='dvf'"
        ).fetchone()[0]
        if not attached:
            con.execute(f"ATTACH '{escaped}' AS dvf (READ_ONLY)")
        con.execute("CREATE OR REPLACE TEMP VIEW prix_reference AS SELECT * FROM dvf.prix_reference")
        con.execute("CREATE OR REPLACE TEMP VIEW ventes_dvf AS SELECT * FROM dvf.ventes_dvf")
    rents_path = Path(os.getenv("RENTS_DB", "data/rents.duckdb")).expanduser().resolve()
    if db_path != ":memory:" and rents_path.exists() and rents_path != Path(db_path).expanduser().resolve():
        escaped = str(rents_path).replace("'", "''")
        attached = con.execute(
            "SELECT count(*) FROM duckdb_databases() WHERE database_name='rents'"
        ).fetchone()[0]
        if not attached:
            con.execute(f"ATTACH '{escaped}' AS rents (READ_ONLY)")
        con.execute("CREATE OR REPLACE TEMP VIEW loyer_reference_commune AS SELECT * FROM rents.loyer_reference_commune")
