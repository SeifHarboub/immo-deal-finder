# Dictionnaire des données

## Principe de conservation

Chaque réponse Leboncoin suit deux chemins simultanés :

1. la couche Bronze `raw_leboncoin` conserve les champs natifs et `raw_payload`,
   copie JSON complète de l'annonce telle que reçue ;
2. la couche Silver `annonces_stg` extrait les champs stables nécessaires aux
   comparaisons entre plateformes.

Ainsi, l'ajout futur d'un champ au schéma commun ne nécessite pas de scraper à
nouveau les anciennes annonces tant que ce champ était présent dans le JSON brut.

## `raw_leboncoin` — Bronze

| Groupe | Champs capturés | Utilité |
|---|---|---|
| Identité | `list_id`, `url`, `category_id`, `status` | déduplication et état de l'annonce |
| Texte | `subject`, `body` | titre, description, analyse des travaux/mots-clés |
| Prix | `price` | prix affiché |
| Bien | `attr_real_estate_type`, `attr_square`, `attr_land`, `attr_rooms`, `attr_bedrooms` | typologie et surfaces |
| Énergie | `attr_energy_rate`, `attr_ges` | DPE et GES |
| Équipements | `attr_furnished`, `attr_elevator`, `attr_pool`, `attr_parking` | caractéristiques valorisantes |
| Position | `zipcode`, `city`, `lat`, `lng` | jointure DVF et cartographie |
| Vendeur | `seller_type`, `seller_name`, `owner_json` | particulier/professionnel |
| Médias | `image_count`, `images_json` | photos et contrôle de qualité |
| Dates | `first_publication_date`, `index_date`, `expiration_date`, `_collected_at` | fraîcheur et historique |
| Natif | `attributes_json`, `raw_payload` | conservation sans perte et évolution du schéma |

Les attributs ne sont pas garantis sur chaque annonce : Leboncoin peut omettre
une surface, un DPE, une position précise ou des informations vendeur. Les JSON
complets permettent d'auditer ce qui était réellement disponible à la collecte.
Après chaque synchronisation réussie, `data/schema-report.json` mesure le taux
de remplissage de chaque colonne et inventorie les clés JSON réellement vues sur
un échantillon récent. La commande manuelle équivalente est `schema-report`.

Les tables `raw_orpi`, `raw_laforet`, `raw_iad`, `raw_pointdevente`,
`raw_immonot` et `raw_geolocaux` appliquent le
même principe : schéma natif par source, payload JSON conservé, horodatage de
collecte et mapping indépendant vers `annonces_stg`.

## Couverture des fiches par source

| Source | Informations complémentaires conservées quand publiées |
|---|---|
| Orpi | quartier, séjour, terrain, année, étage, état, jardin/balcon/terrasse, stationnement, copropriété et charges, DPE/GES chiffrés, coûts énergétiques, honoraires, dates de mise en vente/mise à jour, coordonnées approximatives, galerie |
| Laforêt | toutes les caractéristiques visibles, terrain, pièces/chambres, chauffage/cuisine, équipements, DPE/GES chiffrés via le diagnostic chargé séparément, coûts énergétiques, honoraires, coordonnées de carte, galerie et description complète |
| iad | état structuré complet : année, pièces/chambres, DPE/GES chiffrés, coûts énergétiques, copropriété/lots/charges/procédure, étage et équipements, coordonnées quand publiées, date, description intégrale et galerie |
| Immonot | terrain, chauffage, taxe foncière, état, étages, cave/garage, pièces/chambres, DPE/GES, zone cartographique approximative, référence, description et galerie |
| GeoLocaux | nature/type de transaction, prix global et au m², divisibilité, équipements, fiche technique, géolocalisation certifiée, publication, partenaire, description et galerie |
| PointDeVente | prix, surface, loyer hors charges des murs occupés, charges, honoraires, façade, surface RDC, emplacement, catégorie, référence commerciale, description et photos accessibles |

`_parser_version` identifie la version d'extraction. Après un enrichissement,
les fiches d'une ancienne version sont reparcourues automatiquement, même si
leur URL avait déjà été collectée. Une page absente ou un champ non publié reste
`NULL` : aucune valeur n'est inventée.

## `annonces_stg` — Silver multi-sources

Clé logique : `(source, external_id)`. Une nouvelle observation met à jour
l'annonce sans créer de doublon.

Champs communs : source, identifiant externe, catégorie, type de bien, prix,
loyer, surfaces bâtie/terrain, code postal, ville, coordonnées, titre, URL,
description, pièces, chambres, DPE, GES, type/nom du vendeur, nombre d'images,
date de publication, première et dernière observations, statut actif.

## Référentiel DVF

- `dvf_detail` : les 40 champs natifs DVF géolocalisés, conservés dans la base
  nationale séparée `data/dvf.duckdb` ;
- `ventes_dvf` : ventes résidentielles simples et nettoyées avec mutation, date,
  nature, adresse, parcelle, commune, pièces, surfaces, coordonnées, valeur
  foncière et prix au m² ;
- `prix_reference` : référence par code postal + type avec nombre de ventes,
  ventes sur 12 mois, P10, Q1, médiane, Q3, P90, moyenne, dispersion, surface
  médiane, coordonnées et période couverte ;
- `prix_reference_commune` : même statistique au niveau commune, conservée pour
  les annonces disposant d'un code INSEE ou d'un géocodage suffisamment précis ;
- `dvf_metadata` : date d'actualisation, volumétrie, couverture géographique et
  période réellement disponible.

## `affaires` — Gold

Joint l'annonce à `prix_reference`, puis ajoute prix au m², décote par rapport à
la médiane, prix à viser au Q1, niveau, couleur et confiance statistique.

Le front utilise désormais la table matérialisée `deal_analysis`, recalculée en
fin de synchronisation. La référence v4 retient des ventes de même type avec des
tolérances resserrées sur la surface, les pièces, le terrain, la distance et la
récence. Une confiance `fiable` exige également une faible dispersion, une
surface médiane proche, une vente récente et une fiche suffisamment complète.

Les signaux suivants ne provoquent jamais une décote financière inventée : ils
abaissent la confiance ou suspendent le classement avec un motif explicite :
prix nul, décote supérieure à 45 %, viager/enchère/nue-propriété, bien non encore
habitable, offre ou compromis en cours, logement occupé, résidence gérée, surface
Carrez atypique, travaux importants, DPE F/G, terrain manquant, localisation
approximative, procédure ou charges de copropriété élevées.

## Limites qui restent externes aux annonces

Les travaux chiffrés, l'adresse exacte quand la plateforme ne publie qu'une zone,
les risques Géorisques à la parcelle et l'historique complet des baisses de prix
ne peuvent pas être déduits de façon fiable d'une fiche seule. Les champs comme
charges, taxe foncière, honoraires, lots, étage et année sont maintenant capturés
source par source, mais restent `NULL` lorsque l'annonceur ne les renseigne pas.
