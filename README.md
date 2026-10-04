# immo-deal-finder

Outil local et personnel qui collecte des annonces immobilières, les compare aux
ventes réelles DVF du secteur et classe les décotes avec un prix à viser. Les données
restent sur la machine dans DuckDB. Les bases des services vivent dans `data/runtime/`
afin qu'une extension d'éditeur ne puisse pas bloquer les collecteurs. La collecte utilise un délai configurable (2 à 5 s
entre pages, plafond configurable) et ne contourne ni captcha ni protection anti-bot.

Le dictionnaire précis des tables et champs se trouve dans
[`config/data-dictionary.md`](config/data-dictionary.md).

## Tableau de bord local

```bash
source .venv/bin/activate
export PYTHONPATH=src
python -m immo.cli web
```

Ouvrir ensuite `http://127.0.0.1:8000`. Le tableau de bord affiche toutes les
annonces, y compris celles sans référence, et propose recherche, source, type,
secteur, budget, surface, qualité du deal et tri. Pour les maisons et
appartements, il présente une fourchette : plafond d'excellente affaire à 75 %
de la médiane DVF et plafond de bonne affaire à 85 %.

Pour garder le tableau de bord disponible automatiquement après chaque ouverture
de session macOS : `python -m immo.cli web-install`. Pour le désactiver :
`python -m immo.cli web-uninstall`.

## Fonctionnement automatique recommandé

Une synchronisation réalise, dans cet ordre :

1. actualisation DVF si la précédente date de plus de 30 jours ;
2. collecte réseau parallèle des sources définies dans `SYNC_SOURCES` ;
3. écriture Bronze dédoublonnée par lots de 2 000 et suivi de progression ;
4. une seule normalisation par source après la fin des téléchargements ;
5. recalcul complet de `affaires` avec les prix DVF.

Les sources automatiques actuelles sont iad France, Orpi, Laforêt, Immonot,
PointDeVente et Geolocaux. iad, Orpi, Laforêt et Immonot apportent la priorité
résidentielle ; PointDeVente et Geolocaux complètent avec l'immobilier
professionnel. Superimmo est désactivé tant que ses pages renvoient des erreurs
503 persistantes. L'audit
d'accessibilité des principaux sites est conservé dans `config/source-audit.md`.
Leboncoin est désactivé à cause de DataDome. Chaque passage traite au maximum
`AGENCY_MAX_URLS_PER_RUN` nouvelles pages par source, ignore les
URL vues récemment et les revisite après `AGENCY_REVISIT_DAYS` jours.
Les téléchargements de fiches utilisent une file de préchargement bornée. La
concurrence se règle globalement avec `AGENCY_FETCH_WORKERS`, puis par source
avec `AGENCY_FETCH_WORKERS_<SOURCE>` ; `AGENCY_FETCH_WORKERS_CAP` constitue la
borne de sécurité commune. Chaque fin de collecte affiche le nombre de
candidates, requêtes, réponses exploitables et erreurs de parsing afin de
valider le débit sans masquer une dégradation du site source.

Pour activer cette chaîne toutes les six heures sur macOS :

```bash
python -m immo.cli schedule --every-hours 6
```

Cette commande n'est à exécuter qu'une fois. Ensuite macOS lance le travail seul,
même après redémarrage de la session. Les journaux sont dans
`data/scheduler.out.log` et `data/scheduler.err.log`. Un verrou empêche deux
collectes de se chevaucher. Pour désactiver : `python -m immo.cli unschedule`.

Une exécution complète immédiate reste disponible avec `python -m immo.cli sync`.

## Installation

Python 3.11 ou plus récent est requis.

```bash
cd /Users/seifharboub/Projects/immo-deal-finder
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
export PYTHONPATH=src
```

Le chemin DVF utilisé est le chemin départemental officiel `latest` du jeu
« Demandes de valeurs foncières géolocalisées » :
`https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/departements/{dep}.csv.gz`.
Par défaut, `DVF_DEPARTEMENTS=all` et les cinq années disponibles sont importées.
La reconstruction se fait progressivement dans `data/dvf.next.duckdb`, puis
remplace atomiquement `data/dvf.duckdb` : le tableau de bord continue donc à
utiliser l'ancien référentiel pendant l'actualisation. L'Alsace-Moselle et
Mayotte sont absentes de DVF conformément au périmètre de diffusion officiel.
La progression est visible dans `data/dvf-progress.txt`.

## Collecte Leboncoin à grande volumétrie

Le mode normal ne demande aucune URL. Il génère automatiquement les recherches
depuis `DVF_DEPARTEMENTS` dans `.env` :

```bash
python -m immo.cli collect-auto --max-pages 100
```

Plusieurs départements :

```bash
python -m immo.cli collect-auto --departements 75 92 93 94 --max-pages 100
```

Toute la France (exécution très longue à la cadence configurée) :

```bash
python -m immo.cli collect-auto --departements all --max-pages 100
```

Pour les départements avec trop de résultats, le partitionnement par prix est
également généré automatiquement :

```bash
python -m immo.cli collect-auto \
  --departements 75 92 93 94 \
  --tranches-prix '0-200000,200001-400000,400001-700000,700001-1000000,1000001-max' \
  --max-pages 100
```

Cette commande collecte, normalise et lance aussi le scoring lorsque le
référentiel DVF a déjà été chargé. Les URL restent disponibles uniquement pour
des recherches avancées personnalisées.

Une recherche nationale unique est plafonnée par Leboncoin. Pour une couverture
large, créez un fichier d'URL partitionnées : une URL par département ou zone,
puis divisez encore par tranche de prix lorsque le nombre de résultats est trop
élevé. Un exemple se trouve dans `config/leboncoin-searches.example.txt`.

```bash
python -m immo.cli collect \
  --source leboncoin \
  --url-file config/leboncoin-searches.txt \
  --max-pages 100
```

La collecte :

- réutilise un profil Chromium persistant dans `.playwright-profile` ;
- permet de résoudre manuellement le captcha initial ;
- intercepte les réponses de recherche sans contourner DataDome ;
- pagine jusqu'à `--max-pages` ou jusqu'à la dernière page ;
- écrit les annonces dans DuckDB par lots configurables (`BRONZE_BATCH_SIZE`) ;
- journalise succès, échecs, URL et volumétrie dans `collection_runs` ;
- supporte une relance sûre : la normalisation met à jour une annonce existante
  au lieu de la dupliquer.

`LEBONCOIN_HEADLESS=false` est volontairement la valeur par défaut. Une cadence
de 6 à 14 secondes est conservée entre les pages. “Toutes les annonces” ne peut
pas être garanti par le scraper, car le catalogue visible et les plafonds de
résultats sont contrôlés par Leboncoin ; le partitionnement donne la meilleure
couverture réaliste.

## Exécution complète

Les cinq étapes, dans l'ordre :

```bash
python -m immo.cli reference
python -m immo.cli collect --source leboncoin --url 'URL_DE_RECHERCHE_COPIEE_DU_NAVIGATEUR' --zones 93000 --prix-max 300000 --categorie vente
python -m immo.cli normalize --source leboncoin
python -m immo.cli score
python -m immo.cli deals --limit 20
python -m immo.cli stats
```

Leboncoin ouvre Chromium avec `headless=False`. Si DataDome présente un captcha,
résolvez-le manuellement. L'URL de recherche déjà filtrée est plus fiable que la
reconstruction automatique des paramètres.

Pour consulter le référentiel d'un secteur et ses dernières ventes réelles :

```bash
python -m immo.cli secteur --cp 93000 --type Appartement --limit 20
```

La référence DVF conserve le nombre de ventes, Q1, médiane, Q3, première et
dernière dates disponibles. Pour chaque annonce, le moteur retient au maximum
15 mutations proches selon le type, la surface, les pièces, le terrain, la
distance et la récence, puis écarte les prix au m² atypiques par l'IQR. Le niveau
de confiance tient compte du nombre de ventes, de leur dispersion et de leur
distance de comparabilité. `ventes_dvf` conserve aussi les ventes nettoyées avec
date, type, surface, montant, prix au m² et adresse.

Les loyers résidentiels utilisent les indicateurs publics ANIL 2025 hors charges.
Comme ceux-ci décrivent des logements-types (37 m² pour T1-T2, 72 m² pour T3+,
92 m² pour une maison), le loyer total est corrigé de façon non linéaire selon
la surface. L'interface affiche une plage centrale de prévision et classe le
rendement sur son scénario bas prudent ; l'intervalle ANIL complet, plus large,
n'est pas présenté comme une fourchette de négociation.

## Validation sans scraping

```bash
PYTHONPATH=src .venv/bin/python tests/test_mock_pipeline.py
```

Le test crée une base temporaire, charge trois annonces par JSONL, vérifie une
excellente affaire à -25 %, une bonne à -20,6 %, écarte le bien au prix du marché,
teste l'actualisation sans doublon, puis supprime la base. Après installation, `python -m immo.cli deals` affiche
`Aucune affaire.` tant qu'aucune collecte et aucun scoring n'ont été effectués.

## Architecture

`raw_<source>` (bronze) conserve le schéma natif, `annonces_stg` (silver) fournit
le schéma commun et `affaires` (gold) contient classement, couleur, confiance et
prix à viser. L'accès DuckDB est isolé dans `warehouse.py`.

Pour ajouter une autre source compatible : créer
`src/immo/connectors/<source>.py` avec une classe `Connector` enregistrée via
`register(...)`, puis `src/immo/normalize/stg_<source>.sql`. Les modules sont
découverts automatiquement ; le stockage, le scoring DVF, les statistiques et
la CLI ne changent pas. `AgencyJsonLdConnector` fournit déjà le socle sitemap,
sitemap index/gzip et JSON-LD pour les sites d'agences compatibles.
