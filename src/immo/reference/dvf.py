"""Import national et atomique du référentiel DVF géolocalisé."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

from immo.search_plan import all_department_codes


DVF_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/departements/{dep}.csv.gz"


def _departments(value: str) -> list[str]:
    requested = [item.strip().upper() for item in value.split(",") if item.strip()]
    if requested == ["ALL"]:
        # DVF ne couvre pas le Bas-Rhin, le Haut-Rhin, la Moselle ni Mayotte.
        return [code for code in all_department_codes() if code not in {"57", "67", "68", "976"}]
    return requested


def refresh() -> None:
    departments = _departments(os.getenv("DVF_DEPARTEMENTS", "all"))
    years = [x.strip() for x in os.getenv("DVF_YEARS", "2021,2022,2023,2024,2025").split(",") if x.strip()]
    if not departments or not years:
        raise ValueError("DVF_DEPARTEMENTS et DVF_YEARS ne doivent pas être vides")

    target = Path(os.getenv("DVF_DB", "data/dvf.duckdb")).expanduser().resolve()
    build = target.with_name(f"{target.stem}.next{target.suffix}")
    progress = target.with_name("dvf-progress.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    build.unlink(missing_ok=True)

    con = duckdb.connect(str(build))
    try:
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute("SET preserve_insertion_order=false")
        con.execute("SET memory_limit='4GB'")
        imported = 0
        total = len(years) * len(departments)
        for year in years:
            for department in departments:
                url = DVF_URL.format(year=year, dep=department)
                progress.write_text(
                    f"{imported}/{total} · {department} · {year}\n", encoding="utf-8"
                )
                relation = f"read_csv_auto('{url}', all_varchar=true, sample_size=-1, null_padding=true)"
                try:
                    if imported == 0:
                        con.execute(f"CREATE TABLE dvf_detail AS SELECT * FROM {relation}")
                    else:
                        con.execute(f"INSERT INTO dvf_detail BY NAME SELECT * FROM {relation}")
                    imported += 1
                    con.execute("CHECKPOINT")
                except Exception as exc:
                    # Certaines combinaisons année/département peuvent ne pas exister.
                    print(f"DVF ignoré {department}/{year}: {exc}", flush=True)

        if imported == 0:
            raise RuntimeError("Aucun fichier DVF n'a pu être importé")

        progress.write_text("Calcul des ventes comparables…\n", encoding="utf-8")
        con.execute("""
            CREATE TABLE ventes_dvf AS
            WITH typed AS (
                SELECT id_mutation, TRY_CAST(date_mutation AS DATE) AS date_mutation,
                       nature_mutation, TRY_CAST(valeur_fonciere AS DOUBLE) AS valeur_fonciere,
                       code_departement, code_commune, code_postal, nom_commune AS commune,
                       concat_ws(' ', adresse_numero, adresse_suffixe, adresse_nom_voie) AS adresse,
                       adresse_code_voie, id_parcelle, type_local,
                       TRY_CAST(surface_reelle_bati AS DOUBLE) AS surface_bati,
                       TRY_CAST(nombre_pieces_principales AS INTEGER) AS nombre_pieces,
                       TRY_CAST(surface_terrain AS DOUBLE) AS surface_terrain,
                       TRY_CAST(longitude AS DOUBLE) AS longitude,
                       TRY_CAST(latitude AS DOUBLE) AS latitude,
                       count(*) OVER (PARTITION BY id_mutation) AS lignes_mutation,
                       count(*) FILTER (WHERE type_local IN ('Appartement','Maison'))
                           OVER (PARTITION BY id_mutation) AS logements_mutation
                FROM dvf_detail
            )
            SELECT id_mutation, date_mutation, nature_mutation, code_departement,
                   code_commune, code_postal, commune, adresse, adresse_code_voie,
                   id_parcelle, type_local, surface_bati, nombre_pieces, surface_terrain,
                   valeur_fonciere, valeur_fonciere / NULLIF(surface_bati, 0) AS prix_m2,
                   longitude, latitude
            FROM typed
            WHERE nature_mutation='Vente'
              AND type_local IN ('Appartement','Maison')
              AND logements_mutation=1 AND lignes_mutation=1
              AND surface_bati BETWEEN 10 AND 1000
              AND valeur_fonciere > 5000
              AND valeur_fonciere / NULLIF(surface_bati, 0) BETWEEN 200 AND 30000
              AND code_postal IS NOT NULL;

            -- Une mutation commerciale peut contenir plusieurs biens pour un
            -- prix global. Pour un prix au m² exploitable, on ne conserve que
            -- celles avec un unique local professionnel et aucun logement.
            INSERT INTO ventes_dvf
            WITH mutation_commerciale AS (
                SELECT id_mutation,
                       max(TRY_CAST(date_mutation AS DATE)) AS date_mutation,
                       max(nature_mutation) AS nature_mutation,
                       max(code_departement) AS code_departement,
                       max(code_commune) AS code_commune,
                       max(code_postal) AS code_postal,
                       max(nom_commune) AS commune,
                       max(concat_ws(' ', adresse_numero, adresse_suffixe, adresse_nom_voie))
                           FILTER (WHERE type_local='Local industriel. commercial ou assimilé') AS adresse,
                       max(adresse_code_voie)
                           FILTER (WHERE type_local='Local industriel. commercial ou assimilé') AS adresse_code_voie,
                       max(id_parcelle)
                           FILTER (WHERE type_local='Local industriel. commercial ou assimilé') AS id_parcelle,
                       max(TRY_CAST(surface_reelle_bati AS DOUBLE))
                           FILTER (WHERE type_local='Local industriel. commercial ou assimilé') AS surface_bati,
                       max(TRY_CAST(surface_terrain AS DOUBLE)) AS surface_terrain,
                       max(TRY_CAST(valeur_fonciere AS DOUBLE)) AS valeur_fonciere,
                       max(TRY_CAST(longitude AS DOUBLE)) AS longitude,
                       max(TRY_CAST(latitude AS DOUBLE)) AS latitude,
                       count(*) FILTER (WHERE type_local='Local industriel. commercial ou assimilé') AS locaux_commerciaux,
                       count(*) FILTER (WHERE type_local IN ('Appartement','Maison')) AS logements
                FROM dvf_detail
                GROUP BY id_mutation
            )
            SELECT id_mutation, date_mutation, nature_mutation, code_departement,
                   code_commune, code_postal, commune, adresse, adresse_code_voie,
                   id_parcelle, 'Local industriel. commercial ou assimilé' AS type_local,
                   surface_bati, NULL::INTEGER AS nombre_pieces, surface_terrain,
                   valeur_fonciere, valeur_fonciere / NULLIF(surface_bati, 0) AS prix_m2,
                   longitude, latitude
            FROM mutation_commerciale
            WHERE nature_mutation='Vente'
              AND locaux_commerciaux=1 AND logements=0
              AND surface_bati BETWEEN 10 AND 10000
              AND valeur_fonciere > 5000
              AND valeur_fonciere / NULLIF(surface_bati, 0) BETWEEN 100 AND 50000
              AND code_postal IS NOT NULL;

            CREATE TABLE prix_reference_commune AS
            WITH bounds AS (
                SELECT code_departement, code_commune, code_postal, type_local,
                       quantile_cont(prix_m2, .01) AS p01,
                       quantile_cont(prix_m2, .99) AS p99
                FROM ventes_dvf GROUP BY ALL
            ), clean AS (
                SELECT v.* FROM ventes_dvf v JOIN bounds b USING
                    (code_departement, code_commune, code_postal, type_local)
                WHERE v.prix_m2 BETWEEN b.p01 AND b.p99
            )
            SELECT code_departement, code_commune, code_postal, type_local,
                   count(*) AS nb_ventes,
                   count(*) FILTER (WHERE date_mutation >= current_date - INTERVAL '12 months') AS nb_ventes_12m,
                   quantile_cont(prix_m2,.10) AS p10_eur_m2,
                   quantile_cont(prix_m2,.25) AS q1_eur_m2,
                   quantile_cont(prix_m2,.50) AS median_eur_m2,
                   quantile_cont(prix_m2,.75) AS q3_eur_m2,
                   quantile_cont(prix_m2,.90) AS p90_eur_m2,
                   avg(prix_m2) AS moyenne_eur_m2,
                   stddev_samp(prix_m2) AS ecart_type_eur_m2,
                   quantile_cont(surface_bati,.50) AS surface_mediane,
                   min(date_mutation) AS premiere_vente,
                   max(date_mutation) AS derniere_vente,
                   avg(latitude) AS latitude, avg(longitude) AS longitude
            FROM clean GROUP BY code_departement, code_commune, code_postal, type_local
            HAVING count(*) >= 5;

            CREATE TABLE prix_reference AS
            WITH bounds AS (
                SELECT code_departement, code_postal, type_local,
                       quantile_cont(prix_m2, .01) AS p01,
                       quantile_cont(prix_m2, .99) AS p99
                FROM ventes_dvf GROUP BY ALL
            ), clean AS (
                SELECT v.* FROM ventes_dvf v JOIN bounds b USING
                    (code_departement, code_postal, type_local)
                WHERE v.prix_m2 BETWEEN b.p01 AND b.p99
            )
            SELECT code_departement, CAST(NULL AS VARCHAR) AS code_commune,
                   code_postal, type_local, count(*) AS nb_ventes,
                   count(*) FILTER (WHERE date_mutation >= current_date - INTERVAL '12 months') AS nb_ventes_12m,
                   quantile_cont(prix_m2,.10) AS p10_eur_m2,
                   quantile_cont(prix_m2,.25) AS q1_eur_m2,
                   quantile_cont(prix_m2,.50) AS median_eur_m2,
                   quantile_cont(prix_m2,.75) AS q3_eur_m2,
                   quantile_cont(prix_m2,.90) AS p90_eur_m2,
                   avg(prix_m2) AS moyenne_eur_m2,
                   stddev_samp(prix_m2) AS ecart_type_eur_m2,
                   quantile_cont(surface_bati,.50) AS surface_mediane,
                   min(date_mutation) AS premiere_vente,
                   max(date_mutation) AS derniere_vente,
                   avg(latitude) AS latitude, avg(longitude) AS longitude
            FROM clean GROUP BY code_departement, code_postal, type_local
            HAVING count(*) >= 5;

            CREATE INDEX ventes_dvf_cp_type ON ventes_dvf(code_postal, type_local);
            CREATE INDEX prix_reference_cp_type ON prix_reference(code_postal, type_local);
            CREATE TABLE dvf_metadata AS
            SELECT current_localtimestamp() AS refreshed_at,
                   (SELECT count(*) FROM dvf_detail) AS lignes_detail,
                   (SELECT count(*) FROM ventes_dvf) AS ventes_comparables,
                   (SELECT count(DISTINCT code_departement) FROM ventes_dvf) AS departements,
                   (SELECT count(DISTINCT code_postal) FROM prix_reference) AS codes_postaux,
                   (SELECT min(date_mutation) FROM ventes_dvf) AS premiere_vente,
                   (SELECT max(date_mutation) FROM ventes_dvf) AS derniere_vente;
            CHECKPOINT;
        """)
    finally:
        con.close()

    os.replace(build, target)
    progress.write_text("Terminé\n", encoding="utf-8")
