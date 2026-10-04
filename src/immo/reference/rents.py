"""Référentiel communal des loyers résidentiels ANIL 2025."""

import os
from pathlib import Path

import duckdb


FILES = {
    ("appartement", "tous"): "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-145010/pred-app-mef-dhup.csv",
    ("appartement", "1_2_pieces"): "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-144934/pred-app12-mef-dhup.csv",
    ("appartement", "3_pieces_plus"): "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-144951/pred-app3-mef-dhup.csv",
    ("maison", "tous"): "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-145039/pred-mai-mef-dhup.csv",
}


def refresh() -> int:
    """Télécharge les quatre fichiers officiels et remplace la table atomiquement."""
    selects = []
    for (kind, typology), url in FILES.items():
        selects.append(f"""
            SELECT INSEE_C AS code_commune, LIBGEO AS commune,
                   '{kind}' AS type_bien, '{typology}' AS typologie,
                   TRY_CAST(replace(loypredm2, ',', '.') AS DOUBLE) AS loyer_m2_mois,
                   TRY_CAST(replace("lwr.IPm2", ',', '.') AS DOUBLE) AS loyer_m2_bas,
                   TRY_CAST(replace("upr.IPm2", ',', '.') AS DOUBLE) AS loyer_m2_haut,
                   TYPPRED AS niveau_estimation,
                   TRY_CAST(nbobs_com AS INTEGER) AS nb_observations,
                   TRY_CAST(replace(R2_adj, ',', '.') AS DOUBLE) AS r2
            FROM read_csv('{url}', delim=';', header=true, all_varchar=true,
                          quote='"', encoding='CP1252', sample_size=-1)
        """)
    target = Path(os.getenv("RENTS_DB", "data/rents.duckdb")).expanduser().resolve()
    build = target.with_name(f"{target.stem}.next{target.suffix}")
    dvf = Path(os.getenv("DVF_DB", "data/dvf.duckdb")).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    build.unlink(missing_ok=True)
    con = duckdb.connect(str(build))
    try:
        con.execute("INSTALL httpfs; LOAD httpfs")
        if dvf.exists():
            escaped = str(dvf).replace("'", "''")
            con.execute(f"ATTACH '{escaped}' AS dvf (READ_ONLY)")
        con.execute("CREATE TABLE loyer_reference_next_raw AS " + " UNION ALL ".join(selects))
        con.execute("""
            CREATE TABLE loyer_reference_commune AS
            WITH postal AS (
                SELECT code_commune, mode(code_postal) AS code_postal
                FROM dvf.ventes_dvf
                WHERE code_commune IS NOT NULL AND code_postal IS NOT NULL
                GROUP BY code_commune
            )
            SELECT r.code_commune, p.code_postal, r.commune, r.type_bien, r.typologie,
                   r.loyer_m2_mois, r.loyer_m2_bas, r.loyer_m2_haut,
                   r.niveau_estimation, r.nb_observations, r.r2,
                   2025 AS millesime, current_timestamp AS refreshed_at
            FROM loyer_reference_next_raw r LEFT JOIN postal p USING (code_commune)
            WHERE r.loyer_m2_mois > 0
        """)
        con.execute("DROP TABLE loyer_reference_next_raw")
        con.execute("CREATE INDEX loyer_ref_cp_type ON loyer_reference_commune(code_postal, type_bien, typologie)")
        count = con.execute("SELECT count(*) FROM loyer_reference_commune").fetchone()[0]
        con.execute("CHECKPOINT")
    finally:
        con.close()
    os.replace(build, target)
    return count
