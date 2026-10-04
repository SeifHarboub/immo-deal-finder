"""Classement des opportunités par stratégie d'investissement.

Cinq lectures sont calculées pour chaque annonce, selon ce qui s'applique :

- décote : prix au m² comparé aux ventes DVF réellement comparables ;
- rendement : rendement net et cash-flow d'un achat locatif résidentiel ou
  d'un immeuble de rapport, travaux et mise aux normes DPE inclus ;
- murs : rendement des murs commerciaux, loyer déclaré ou estimé ;
- fonds : multiples prix / EBE et prix / chiffre d'affaires d'un fonds ;
- enchère : décote du prix d'adjudication probable, pas de la mise à prix.

Le score global retient la meilleure stratégie applicable, puis ajoute des
bonus transparents (baisse de prix, nouveauté). Toutes les hypothèses
financières se règlent dans `.env` (préfixe INVEST_).
"""

from __future__ import annotations

import os


def _env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def assumptions() -> dict[str, float]:
    return {
        "notaire_pct": _env("INVEST_FRAIS_NOTAIRE_PCT", 7.5),
        "frais_fonds_pct": _env("INVEST_FRAIS_FONDS_PCT", 5.0),
        "taux_credit": _env("INVEST_TAUX_CREDIT", 3.4),
        "duree_credit": _env("INVEST_DUREE_CREDIT_ANS", 20),
        "apport_pct": _env("INVEST_APPORT_PCT", 10),
        "vacance_mois": _env("INVEST_VACANCE_MOIS", 1),
        "gestion_pct": _env("INVEST_GESTION_PCT", 0),
        "entretien_pct": _env("INVEST_ENTRETIEN_PCT", 5),
        "pno_eur": _env("INVEST_PNO_EUR", 200),
        "tf_mois_loyer": _env("INVEST_TF_MOIS_LOYER", 1.2),
        "copro_m2_an": _env("INVEST_CHARGES_COPRO_M2_AN", 25),
        "copro_non_recup": _env("INVEST_PART_COPRO_NON_RECUPERABLE", 0.35),
        "travaux_renovation_m2": _env("INVEST_TRAVAUX_RENOVATION_M2", 600),
        "travaux_dpe_f_m2": _env("INVEST_TRAVAUX_DPE_F_M2", 200),
        "travaux_dpe_g_m2": _env("INVEST_TRAVAUX_DPE_G_M2", 350),
        "ratio_adjudication": _env("INVEST_RATIO_ADJUDICATION", 1.65),
        "doublon_ecart_prix": _env("DEDUP_ECART_PRIX", 0.03),
    }


TEXT = "lower(strip_accents(coalesce(a.titre,'') || ' ' || coalesce(a.description,'')))"

ATYPIQUE = "viager|bouquet|rente viag|nue.?propriete|vente a terme|parts sociales|quote.?part|usufruit"
ENCHERE = "adjudication|enchere|mise a prix|vente interactive"
NON_HABITABLE = (
    "ancien garage|garage.?atelier|garages attenants|plateau brut|plateaux.{0,50}a rendre habitable|"
    "a rendre habitable|volume brut|local a transformer|transformation complete en espace habitable|"
    "a transformer en habitation|changement de destination|raccordement.{0,40}a prevoir|"
    "assainissement.{0,40}a prevoir|pouvant etre transforme|transformable en (?:habitation|logement)|"
    "a amenager entierement|grange a renover|ruine"
)
NON_DISPONIBLE = "compromis en cours|sous compromis|sous offre|offre acceptee|vente realisee|bien vendu"
OCCUPE = (
    "vendu loue|vendue louee|vendus loues|vendues louees|bien occupe|logement occupe|occupes par un|"
    "occupees par un|locataire en place|locataires en place|bail en cours"
)
RESIDENCE_GEREE = (
    "residence (?:hoteliere|seniors?|services?|etudiante|de tourisme|d.affaires|de vacances|geree)|"
    "ehpad|loyer garanti|lmnp|lmp\\b|bail commercial (?:avec|aupres d)"
)
# Travaux lourds (≈ INVEST_TRAVAUX_RENOVATION_M2) ; le reste est un rafraîchissement.
TRAVAUX_LOURDS = (
    "a renover entierement|renovation complete|rehabilitation|gros travaux|refection complete|"
    "entierement a renover|tout a refaire|a restaurer|gros oeuvre|toiture a refaire"
)
TRAVAUX = (
    "a renover|a finir de renover|travaux.{0,60}a prevoir|travaux restants|quelques travaux|"
    "necessitant.{0,30}travaux|renovation.{0,40}a prevoir|renovation complete|rehabilitation|"
    "gros travaux|refection complete|remise au gout|a rafraichir"
)

# Formulations qui annulent une mention de travaux (« aucun travaux à prévoir »).
SANS_TRAVAUX = (
    "aucuns? travaux|sans travaux|pas de travaux|aucun gros travaux|travaux (?:deja )?realises|"
    "travaux recents|entierement renove|refait a neuf|renove recemment|renovation recente"
)

CONFIANCE_FIABLE = """
    nb_ventes >= 10 AND score_comparabilite<=.65
    AND (q3_eur_m2-q1_eur_m2)/NULLIF(median_eur_m2,0)<=.28
    AND surface_mediane_reference BETWEEN surface_bati*.85 AND surface_bati*1.15
    AND derniere_vente >= current_date-INTERVAL '2 years'
    AND NOT localisation_approximative
    AND NOT bien_occupe AND NOT bien_non_habitable AND NOT residence_geree
    AND NOT non_disponible AND NOT surface_atypique
    AND NOT travaux_probables AND NOT procedure_copropriete
    AND upper(coalesce(dpe,'')) BETWEEN 'A' AND 'E'
    AND length(coalesce(description,''))>=250
    AND coalesce(details_json,'{}')<>'{}'
    AND NOT coalesce(charges_copropriete_annuelles/NULLIF(surface_bati,0)>35, false)
    AND (type_bien<>'maison' OR surface_terrain IS NOT NULL)
"""
CONFIANCE_INDICATIVE = """
    nb_ventes >= 7 AND score_comparabilite<=1.05
    AND (q3_eur_m2-q1_eur_m2)/NULLIF(median_eur_m2,0)<=.45
"""


def _clamp(expression: str) -> str:
    return f"greatest(0.0, least(100.0, {expression}))"


def deals_sql() -> str:
    """Requête complète ; elle finit par une CTE `deals`."""
    h = assumptions()
    monthly_rate = h["taux_credit"] / 1200
    months = int(h["duree_credit"] * 12)
    # Mensualité d'un crédit amortissable de 1 € : r / (1 - (1+r)^-n).
    annuity = monthly_rate / (1 - (1 + monthly_rate) ** -months) if monthly_rate else 1 / months
    occupancy = (12 - h["vacance_mois"]) / 12
    return f"""
WITH prix_signaux AS (
    SELECT source, external_id,
           arg_min(prix, observed_at) AS prix_initial,
           max(prix) AS prix_max_observe,
           count(*) FILTER (WHERE baisse) AS nb_baisses,
           max(observed_at) FILTER (WHERE baisse) AS derniere_baisse
    FROM (
        SELECT *, prix < lag(prix) OVER (
            PARTITION BY source, external_id ORDER BY observed_at
        ) AS baisse
        FROM prix_historique WHERE prix > 0
    ) GROUP BY ALL
), ratio_encheres AS (
    -- Prix d'adjudication observé ÷ mise à prix, par type, sur les ventes
    -- passées réellement collectées ; valeurs par défaut sous 20 résultats.
    SELECT type_bien, median(prix_adjuge / prix) AS ratio, count(*) AS n
    FROM annonces_stg
    WHERE prix_adjuge > 0 AND prix > 0 AND mode_vente LIKE 'enchere%'
      AND prix_adjuge / prix BETWEEN .3 AND 15
    GROUP BY type_bien HAVING count(*) >= 20
), rent_model AS (
    SELECT a.*,
           coalesce(f.segment,
                CASE WHEN a.type_bien IN ('appartement','maison') THEN 'residentiel'
                     WHEN a.type_bien='fonds_commerce' THEN 'fonds'
                     WHEN a.type_bien IN ('local_commercial','bureau') THEN 'murs'
                     WHEN a.type_bien='immeuble' THEN 'immeuble'
                     WHEN a.type_bien='terrain' THEN 'terrain' ELSE 'autre' END) AS segment,
           f.loyer_annuel_declare, f.rendement_declare, f.chiffre_affaires, f.ebe,
           f.resultat, f.taxe_fonciere, f.travaux_annonces, f.nb_lots,
           coalesce(f.bien_loue, false) AS bien_loue_declare,
           coalesce(f.droit_au_bail, false) AS droit_au_bail,
           r.nb_ventes, r.q1_eur_m2, r.median_eur_m2, r.q3_eur_m2,
           r.premiere_vente, r.derniere_vente,
           r.surface_mediane_reference, r.score_comparabilite,
           rent.loyer_m2_mois, rent.loyer_m2_bas, rent.loyer_m2_haut,
           rent.niveau_estimation AS niveau_estimation_loyer,
           rent.nb_observations AS nb_observations_loyer,
           rent.r2 AS r2_loyer, rent.millesime AS millesime_loyer,
           ps.prix_initial, ps.prix_max_observe, coalesce(ps.nb_baisses, 0) AS nb_baisses,
           ps.derniere_baisse,
           coalesce(a.mode_vente, 'gre_a_gre') AS mode_vente_effectif,
           coalesce((SELECT ratio FROM ratio_encheres re WHERE re.type_bien=a.type_bien),
                    CASE a.type_bien WHEN 'appartement' THEN 2.06 WHEN 'maison' THEN 1.46
                         ELSE {h['ratio_adjudication']} END) AS ratio_adjudication,
           CASE WHEN a.type_bien='appartement' AND a.nb_pieces BETWEEN 1 AND 2 THEN 37.0
                WHEN a.type_bien='appartement' AND a.nb_pieces>=3 THEN 72.0
                WHEN a.type_bien='appartement' THEN 52.0
                WHEN a.type_bien='maison' THEN 92.0
                ELSE a.surface_bati END AS surface_loyer_reference,
           CASE WHEN a.type_bien='appartement' THEN .75
                WHEN a.type_bien='maison' THEN .80 ELSE 1.0 END AS elasticite_surface_loyer,
           CASE WHEN a.categorie='vente' THEN a.prix / NULLIF(a.surface_bati, 0) END AS prix_m2,
           lower(coalesce(a.details_json,'')) LIKE '%approximative%' AS localisation_approximative,
           regexp_matches({TEXT}, '{ATYPIQUE}') AS transaction_atypique,
           -- coalesce : un NULL ici excluait toute annonce sans mode de vente
           -- du filtre « hors enchères ».
           coalesce(a.mode_vente IN ('enchere_judiciaire','enchere_notariale','vente_interactive','cession_publique'), false)
               OR (a.mode_vente IS NULL AND coalesce(regexp_matches({TEXT}, '{ENCHERE}'), false)) AS vente_encheres,
           regexp_matches({TEXT}, '{OCCUPE}') OR coalesce(f.bien_loue, false) AS bien_occupe,
           regexp_matches({TEXT}, '{NON_HABITABLE}') AS bien_non_habitable,
           regexp_matches({TEXT}, '{RESIDENCE_GEREE}') AS residence_geree,
           regexp_matches({TEXT}, '{NON_DISPONIBLE}')
               OR lower(coalesce(a.details_json,'')) LIKE '%"sous compromis": "oui"%' AS non_disponible,
           regexp_matches(lower(strip_accents(coalesce(a.description,''))),
                'loi carrez.{{0,80}}loggia|loggia.{{0,80}}loi carrez|ancienne loggia') AS surface_atypique,
           (regexp_matches({TEXT}, '{TRAVAUX}') AND NOT regexp_matches({TEXT}, '{SANS_TRAVAUX}'))
               OR f.travaux_annonces > 0 AS travaux_probables,
           lower(coalesce(json_extract_string(try_cast(a.details_json AS JSON),
                '$."Procédure de copropriété en cours"'), 'non'))='oui' AS procedure_copropriete,
           f.charges_copro_annuelles AS charges_copropriete_annuelles,
           TRY_CAST(json_extract_string(try_cast(a.details_json AS JSON), '$."Loyer du bail annuel"') AS DOUBLE)
               AS loyer_bail_annuel_structure
    FROM annonces_stg a
    LEFT JOIN annonce_finance f ON f.source=a.source AND f.external_id=a.external_id
    LEFT JOIN annonce_reference r ON r.source=a.source AND r.external_id=a.external_id
    LEFT JOIN annonce_loyer_reference rent ON rent.source=a.source AND rent.external_id=a.external_id
    LEFT JOIN prix_signaux ps ON ps.source=a.source AND ps.external_id=a.external_id
    WHERE a.categorie = 'vente' OR a.type_bien IN ('fonds_commerce', 'local_commercial', 'bureau')
), flagged AS (
    SELECT *,
           -- Une enchère se compare au prix probable d'adjudication, jamais à la mise à prix.
           CASE WHEN vente_encheres AND prix_adjuge IS NULL THEN prix * ratio_adjudication
                ELSE coalesce(prix_adjuge, prix) END AS prix_compare,
           -- Un fonds ou un terrain s'analyse sans surface bâtie.
           (prix IS NULL OR prix <= 0
            OR (segment NOT IN ('fonds','terrain') AND coalesce(surface_bati, 0) <= 0)
            -- Sous 14 m², un « appartement » est presque toujours un parking,
            -- une cave ou une chambre de service.
            OR (segment='residentiel' AND surface_bati < 14)
            OR bien_non_habitable OR non_disponible OR transaction_atypique) AS donnees_invalides
    FROM rent_model
), base AS (
    SELECT flagged.*,
           donnees_invalides OR (median_eur_m2 IS NOT NULL AND segment='residentiel'
               AND prix_compare <= surface_bati * median_eur_m2 * 0.55) AS prix_trop_bas,
           CASE WHEN type_bien IN ('local_commercial','bureau') OR segment='murs'
                THEN loyer_m2_mois * surface_bati
                ELSE loyer_m2_mois * surface_loyer_reference
                     * pow(greatest(.25, least(1.6, surface_bati/surface_loyer_reference)),
                           elasticite_surface_loyer) END AS loyer_mensuel_estime,
           CASE WHEN type_bien IN ('local_commercial','bureau') OR segment='murs'
                THEN loyer_m2_bas * surface_bati
                ELSE loyer_m2_mois * surface_loyer_reference
                     * pow(greatest(.25, least(1.6, surface_bati/surface_loyer_reference)),
                           elasticite_surface_loyer)
                     * exp(ln(NULLIF(loyer_m2_bas,0)/NULLIF(loyer_m2_mois,0)) * .654) END AS loyer_mensuel_bas,
           CASE WHEN type_bien IN ('local_commercial','bureau') OR segment='murs'
                THEN loyer_m2_haut * surface_bati
                ELSE loyer_m2_mois * surface_loyer_reference
                     * pow(greatest(.25, least(1.6, surface_bati/surface_loyer_reference)),
                           elasticite_surface_loyer)
                     * exp(ln(NULLIF(loyer_m2_haut,0)/NULLIF(loyer_m2_mois,0)) * .654) END AS loyer_mensuel_haut,
           loyer_m2_bas * surface_bati AS loyer_mensuel_bas_officiel,
           loyer_m2_haut * surface_bati AS loyer_mensuel_haut_officiel,
           -- Travaux : montant annoncé, sinon forfait rénovation, sinon mise aux
           -- normes DPE (G interdit à la location depuis 2025, F en 2028).
           CASE WHEN travaux_annonces > 0 THEN travaux_annonces
                WHEN regexp_matches(lower(strip_accents(coalesce(titre,'') || ' ' || coalesce(description,''))), '{TRAVAUX_LOURDS}')
                     AND NOT regexp_matches(lower(strip_accents(coalesce(titre,'') || ' ' || coalesce(description,''))), '{SANS_TRAVAUX}')
                     AND segment IN ('residentiel','immeuble')
                     THEN surface_bati * {h['travaux_renovation_m2']}
                WHEN regexp_matches(lower(strip_accents(coalesce(titre,'') || ' ' || coalesce(description,''))), '{TRAVAUX}')
                     AND NOT regexp_matches(lower(strip_accents(coalesce(titre,'') || ' ' || coalesce(description,''))), '{SANS_TRAVAUX}')
                     AND segment IN ('residentiel','immeuble')
                     THEN surface_bati * {h['travaux_renovation_m2']} * .4
                WHEN upper(coalesce(dpe,''))='G' AND segment IN ('residentiel','immeuble')
                     THEN surface_bati * {h['travaux_dpe_g_m2']}
                WHEN upper(coalesce(dpe,''))='F' AND segment IN ('residentiel','immeuble')
                     THEN surface_bati * {h['travaux_dpe_f_m2']}
                ELSE 0 END AS travaux_estimes
    FROM flagged
), finance AS (
    SELECT base.*,
           -- Loyer retenu : loyer réellement encaissé s'il est publié, sinon
           -- le bas de la plage de prévision (lecture prudente).
           CASE WHEN segment IN ('residentiel','immeuble','murs') AND loyer_annuel_declare > 0
                     AND bien_loue_declare THEN loyer_annuel_declare / 12
                -- Parties communes, escaliers et lots non louables d'un immeuble.
                -- Une estimation ANIL n'a de sens que pour un bien d'habitation
                -- ordinaire : hors de ces bornes, seul un loyer publié compte.
                WHEN segment IN ('residentiel','immeuble')
                     AND (prix / NULLIF(surface_bati, 0) < 300
                          OR surface_bati > CASE segment WHEN 'immeuble' THEN 1500 ELSE 400 END)
                     THEN NULL
                WHEN segment='immeuble' THEN loyer_mensuel_bas * .85
                ELSE loyer_mensuel_bas END AS loyer_mensuel_retenu,
           segment IN ('residentiel','immeuble','murs') AND loyer_annuel_declare > 0
               AND bien_loue_declare AS loyer_reel,
           prix * (1 + CASE WHEN segment='fonds' THEN {h['frais_fonds_pct']} ELSE {h['notaire_pct']} END / 100)
               + travaux_estimes AS cout_total
    FROM base
), charges AS (
    SELECT finance.*,
           coalesce(taxe_fonciere, loyer_mensuel_retenu * {h['tf_mois_loyer']}) AS taxe_fonciere_retenue,
           CASE WHEN type_bien='appartement' AND segment='residentiel'
                THEN coalesce(charges_copropriete_annuelles, surface_bati * {h['copro_m2_an']})
                     * {h['copro_non_recup']} ELSE 0 END AS copro_non_recuperable,
           loyer_mensuel_retenu * 12 * {occupancy} AS loyer_annuel_encaisse
    FROM finance
), returns AS (
    SELECT charges.*,
           loyer_annuel_encaisse
             - taxe_fonciere_retenue - copro_non_recuperable - {h['pno_eur']}
             - loyer_annuel_encaisse * ({h['entretien_pct']} + {h['gestion_pct']}) / 100
             AS revenu_net_annuel,
           cout_total * (1 - {h['apport_pct']} / 100) * {annuity} AS mensualite_credit
    FROM charges
), deals AS (
    SELECT returns.*,
           1200 * loyer_mensuel_estime / NULLIF(prix,0) AS rendement_brut_affiche,
           1200 * loyer_mensuel_bas / NULLIF(prix,0) AS rendement_brut_prudent,
           1200 * loyer_mensuel_haut / NULLIF(prix,0) AS rendement_brut_haut,
           1200 * loyer_mensuel_estime / NULLIF(surface_bati*median_eur_m2,0) AS rendement_brut_marche,
           1200 * loyer_mensuel_retenu / NULLIF(prix,0) AS rendement_brut_retenu,
           100 * revenu_net_annuel / NULLIF(cout_total,0) AS rendement_net,
           revenu_net_annuel / 12 - mensualite_credit AS cashflow_mensuel,
           prix / NULLIF(chiffre_affaires,0) AS multiple_ca,
           prix / NULLIF(ebe,0) AS multiple_ebe,
           coalesce(loyer_bail_annuel_structure,
                    CASE WHEN segment='fonds' THEN loyer_annuel_declare END)
               / NULLIF(chiffre_affaires,0) AS poids_loyer_ca,
           -- Décote nette : les travaux s'ajoutent au prix, et un logement
           -- occupé se vend normalement environ 15 % sous la valeur libre.
           CASE WHEN NOT prix_trop_bas AND segment IN ('residentiel','immeuble','murs')
                THEN ((prix_compare + CASE WHEN segment='residentiel' THEN travaux_estimes ELSE 0 END)
                      / NULLIF(surface_bati * median_eur_m2
                               * CASE WHEN segment='residentiel' AND bien_occupe THEN .85 ELSE 1 END, 0)) - 1 END AS decote,
           CASE WHEN NOT prix_trop_bas AND segment IN ('residentiel','immeuble','murs')
                THEN (prix_compare / NULLIF(surface_bati, 0) - median_eur_m2) / NULLIF(median_eur_m2, 0) END AS decote_brute,
           100 * (prix_max_observe - prix) / NULLIF(prix_max_observe, 0) AS baisse_prix_pct,
           date_diff('day', COALESCE(published_at, first_seen_at), now()) AS jours_en_ligne,
           CASE
             WHEN prix IS NULL OR prix <= 0 THEN 'Prix de vente nul ou absent'
             WHEN surface_bati IS NULL OR surface_bati <= 0 THEN 'Surface absente ou incohérente'
             WHEN segment='residentiel' AND surface_bati < 14 THEN 'Surface inférieure à 14 m² : parking, cave ou chambre de service probable'
             WHEN transaction_atypique THEN 'Viager, nue-propriété ou parts : comparaison directe non valable'
             WHEN bien_non_habitable THEN 'Le bien n’est pas encore un logement habitable comparable aux ventes DVF résidentielles'
             WHEN non_disponible THEN 'L’annonce indique qu’une offre ou un compromis est déjà en cours'
             WHEN median_eur_m2 IS NOT NULL AND segment='residentiel'
                  AND prix_compare <= surface_bati * median_eur_m2 * .55
                  THEN 'Décote supérieure à 45 % : prix, état ou nature de la vente à contrôler'
             ELSE NULL END AS motif_verification,
           concat_ws(' · ',
             CASE WHEN vente_encheres THEN 'Vente aux enchères : prix final probable ≈ ' || round(ratio_adjudication, 2) || ' × mise à prix (médiane des adjudications observées)' END,
             CASE WHEN bien_occupe AND segment='residentiel' THEN 'Bien vendu occupé' END,
             CASE WHEN residence_geree THEN 'Résidence gérée ou bail commercial' END,
             CASE WHEN surface_atypique THEN 'Surface Carrez différente de la surface annoncée' END,
             CASE WHEN travaux_probables THEN 'Travaux probables' END,
             CASE WHEN procedure_copropriete THEN 'Procédure de copropriété en cours' END,
             CASE WHEN charges_copropriete_annuelles/NULLIF(surface_bati,0)>35 THEN 'Charges de copropriété élevées' END,
             CASE WHEN upper(coalesce(dpe,''))='G' THEN 'DPE G : location interdite sans travaux' END,
             CASE WHEN upper(coalesce(dpe,''))='F' THEN 'DPE F : location interdite à partir de 2028' END,
             CASE WHEN type_bien='maison' AND surface_terrain IS NULL THEN 'Terrain non renseigné' END,
             CASE WHEN localisation_approximative THEN 'Localisation approximative' END,
             CASE WHEN segment='fonds' AND chiffre_affaires IS NULL THEN 'Chiffre d’affaires non publié' END,
             CASE WHEN segment='fonds' AND ebe / NULLIF(chiffre_affaires, 0) > .45 THEN 'EBE supérieur à 45 % du chiffre d’affaires : à justifier par la liasse fiscale' END,
             CASE WHEN segment='fonds' AND poids_loyer_ca > .12 THEN 'Loyer élevé au regard du chiffre d’affaires' END
           ) AS risques_evaluation,
           CASE WHEN NOT prix_trop_bas THEN surface_bati * q1_eur_m2 END AS plafond_q1,
           CASE WHEN NOT prix_trop_bas THEN surface_bati * median_eur_m2 * 0.75 END AS plafond_excellente,
           CASE WHEN NOT prix_trop_bas THEN surface_bati * median_eur_m2 * 0.85 END AS plafond_bonne,
           CASE WHEN prix_trop_bas THEN 'indisponible'
                WHEN segment NOT IN ('residentiel','immeuble','murs') THEN 'indisponible'
                WHEN segment IN ('murs','immeuble') AND nb_ventes IS NOT NULL THEN 'indicative'
                WHEN {CONFIANCE_FIABLE} THEN 'fiable'
                WHEN {CONFIANCE_INDICATIVE} THEN 'indicative'
                WHEN nb_ventes IS NOT NULL THEN 'fragile'
                ELSE 'indisponible' END AS confiance
    FROM returns
), scored AS (
    SELECT deals.*,
           -- Le DVF commercial mélange boutiques, bureaux et entrepôts : sa
           -- décote reste affichée mais ne classe que le résidentiel.
           CASE WHEN decote IS NULL OR median_eur_m2 IS NULL OR segment <> 'residentiel' THEN NULL
                ELSE round({_clamp('-100 * decote / 0.30')}
                     * CASE confiance WHEN 'fiable' THEN 1.0 WHEN 'indicative' THEN .75 ELSE .45 END
                     -- Sans terrain ni description, la comparaison reste grossière.
                     * CASE WHEN type_bien='maison' AND surface_terrain IS NULL THEN .85 ELSE 1 END
                     * CASE WHEN length(coalesce(description,'')) < 120 THEN .8 ELSE 1 END) END
               AS score_decote,
           CASE WHEN segment NOT IN ('residentiel','immeuble') OR prix_trop_bas
                     OR rendement_net IS NULL OR residence_geree THEN NULL
                -- Un rendement élevé obtenu en payant au-dessus du marché expose
                -- à une moins-value à la revente : le score est réduit d'autant.
                -- Un loyer seulement estimé ne peut pas, à lui seul, désigner une
                -- excellente affaire : plafond à 70 sans loyer réel publié.
                ELSE round(least(CASE WHEN loyer_reel THEN 100 ELSE 70 END,
                     {_clamp('(rendement_net - 3) / (8 - 3) * 100')}
                     * CASE WHEN decote > 0 THEN greatest(.35, 1 - decote) ELSE 1.0 END
                     * CASE WHEN loyer_reel THEN 1.0
                            WHEN nb_observations_loyer >= 30 AND coalesce(r2_loyer, 1) >= .5 THEN .85
                            ELSE .65 END
                     * CASE WHEN length(coalesce(description,'')) < 120 THEN .8 ELSE 1 END)) END AS score_rendement,
           CASE WHEN segment <> 'murs' OR donnees_invalides OR rendement_net IS NULL THEN NULL
                ELSE round({_clamp('(rendement_net - 4) / (10 - 4) * 100')}
                     * CASE WHEN loyer_reel THEN 1.0
                            WHEN nb_observations_loyer >= 8 THEN .6 ELSE .45 END) END AS score_murs,
           CASE WHEN segment <> 'fonds' OR prix IS NULL OR prix <= 0 THEN NULL
                -- Petits commerces : 2 à 4 fois l'EBE retraité est l'usage. Un
                -- multiple sous 0,8 est presque toujours une erreur de saisie et
                -- un EBE au-delà de 45 % du chiffre d'affaires reste à prouver.
                WHEN ebe > 0 AND prix / ebe BETWEEN .8 AND 30
                     THEN round({_clamp('(4.5 - prix / ebe) / (4.5 - 1.5) * 100')}
                          * CASE WHEN poids_loyer_ca > .12 THEN .7 ELSE 1.0 END
                          * CASE WHEN ebe / NULLIF(chiffre_affaires, 0) > .45 THEN .6 ELSE 1.0 END)
                -- Sans EBE, le chiffre d'affaires ne dit rien de la marge : au mieux « bonne ».
                WHEN chiffre_affaires > 0
                     THEN round({_clamp('(1.0 - prix / chiffre_affaires) / (1.0 - 0.25) * 60')}
                          * CASE WHEN poids_loyer_ca > .12 THEN .7 ELSE 1.0 END)
                END AS score_fonds
    FROM deals
), ranked AS (
    SELECT scored.*,
           greatest(coalesce(score_decote, -1), coalesce(score_rendement, -1),
                    coalesce(score_murs, -1), coalesce(score_fonds, -1)) AS meilleur_score,
           CASE greatest(coalesce(score_decote, -1), coalesce(score_rendement, -1),
                         coalesce(score_murs, -1), coalesce(score_fonds, -1))
                WHEN -1 THEN NULL
                WHEN score_fonds THEN 'fonds'
                WHEN score_murs THEN 'murs'
                WHEN score_rendement THEN 'rendement'
                ELSE 'decote' END AS strategie,
           CASE WHEN baisse_prix_pct >= 10 THEN 8 WHEN baisse_prix_pct >= 5 THEN 5 ELSE 0 END
             + CASE WHEN jours_en_ligne <= 2 THEN 3 ELSE 0 END AS bonus,
           concat_ws(' · ',
             CASE WHEN baisse_prix_pct >= 1 THEN 'Prix baissé de ' || round(baisse_prix_pct, 1) || ' %' END,
             CASE WHEN jours_en_ligne <= 2 THEN 'Nouvelle annonce' END,
             CASE WHEN loyer_reel THEN 'Loyer réel publié' END,
             CASE WHEN vente_encheres THEN 'Enchère' END
           ) AS signaux,
           coalesce(active IS NOT false AND prix > 0 AND surface_bati > 0
                    AND code_postal IS NOT NULL, false) AS dedoublonnable
    FROM scored
), clustered AS (
    -- Une même annonce publiée sur plusieurs plateformes : même type, même
    -- commune, surface arrondie identique et prix à moins de 3 %.
    SELECT ranked.*,
           CASE WHEN dedoublonnable
                THEN hash(type_bien, code_postal, round(surface_bati), sum(nouveau_groupe) OVER (
                    PARTITION BY type_bien, code_postal, round(surface_bati)
                    ORDER BY prix, source, external_id ROWS UNBOUNDED PRECEDING)) END AS groupe_doublon
    FROM (
        SELECT ranked.*,
               CASE WHEN prix > {1 + h['doublon_ecart_prix']} * lag(prix) OVER (
                    PARTITION BY type_bien, code_postal, round(surface_bati) ORDER BY prix, source, external_id)
                    OR lag(prix) OVER (PARTITION BY type_bien, code_postal, round(surface_bati)
                                       ORDER BY prix, source, external_id) IS NULL
                    THEN 1 ELSE 0 END AS nouveau_groupe
        FROM ranked WHERE dedoublonnable
        UNION ALL BY NAME
        SELECT ranked.*, 1 AS nouveau_groupe FROM ranked WHERE NOT dedoublonnable
    ) ranked
), final AS (
    SELECT * EXCLUDE (nouveau_groupe),
           CASE WHEN meilleur_score < 0 THEN NULL ELSE least(100, meilleur_score + bonus) END AS score_global,
           CASE WHEN groupe_doublon IS NULL THEN 1 ELSE row_number() OVER (
               PARTITION BY groupe_doublon
               ORDER BY length(coalesce(description,'')) + 200 * coalesce(image_count,0)
                        + CASE WHEN lat IS NOT NULL THEN 500 ELSE 0 END DESC, source) END AS rang_doublon,
           CASE WHEN groupe_doublon IS NULL THEN 1 ELSE count(*) OVER (PARTITION BY groupe_doublon) END
               AS nb_publications,
           CASE WHEN groupe_doublon IS NOT NULL THEN
               list_sort(list_distinct(list(source) OVER (PARTITION BY groupe_doublon))) END AS sources_doublon
    FROM clustered
), deals_final AS (
    SELECT *,
           -- Compatibilité avec l'interface et les filtres historiques.
           score_decote AS score_opportunite,
           CASE
             WHEN categorie <> 'vente' AND segment <> 'fonds' THEN 'a_analyser'
             WHEN prix_trop_bas AND segment NOT IN ('terrain','autre') THEN 'a_verifier'
             WHEN score_global IS NULL THEN 'a_analyser'
             WHEN score_global >= 75 THEN 'excellente'
             WHEN score_global >= 55 THEN 'bonne'
             WHEN score_global >= 30 THEN 'correcte'
             ELSE 'hors_cible' END AS niveau_affaire
    FROM final
)
"""


def materialize(con) -> int:
    """Recalcule `deal_analysis`, lue directement par l'interface."""
    from immo.lifecycle import ensure_tables
    ensure_tables(con)
    from immo.finance import ensure_table
    ensure_table(con)
    con.execute("CREATE OR REPLACE TABLE deal_analysis_next AS " + deals_sql() + " SELECT * FROM deals_final")
    # Remplacement en une transaction : l'interface ne voit jamais de table vide.
    con.execute("BEGIN")
    con.execute("DROP TABLE IF EXISTS deal_analysis")
    con.execute("ALTER TABLE deal_analysis_next RENAME TO deal_analysis")
    con.execute("COMMIT")
    return con.execute("SELECT count(*) FROM deal_analysis").fetchone()[0]
