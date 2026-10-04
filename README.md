# immo-deal-finder

Outil local et personnel qui collecte les annonces de 27 plateformes, écarte les
annonces retirées et les doublons entre sites, puis classe les vraies affaires selon
la stratégie d'investissement qui s'applique. Les données restent sur la machine
dans DuckDB (`data/runtime/`). La collecte respecte robots.txt, garde des délais
entre requêtes et ne contourne ni captcha ni protection anti-bot.

Le dictionnaire des tables et champs se trouve dans
[`config/data-dictionary.md`](config/data-dictionary.md).

## Stratégies évaluées

Chaque annonce reçoit un score sur 100 pour chaque stratégie applicable ; le score
global retient la meilleure, plus de petits bonus visibles (baisse de prix,
nouveauté). Excellente ≥ 75, bonne ≥ 55.

| Stratégie | Ce qui est mesuré |
|---|---|
| Sous le marché (résidentiel) | prix au m² comparé aux 15 ventes DVF les plus proches (type, surface, pièces, terrain, distance, récence), confiance selon le nombre et la dispersion des ventes |
| Locatif (résidentiel) | rendement **net** et cash-flow après crédit : loyer réel publié si le bien est loué, sinon bas de la plage ANIL ; frais de notaire, travaux annoncés ou forfaitaires, mise aux normes DPE F/G, taxe foncière, copropriété non récupérable, vacance, entretien, assurance |
| Immeubles de rapport | même calcul, loyers réels publiés ou ANIL appartement sur 85 % de la surface |
| Murs commerciaux | rendement net du loyer publié (bien occupé) ou estimé à partir des locations professionnelles comparables |
| Fonds de commerce | prix / EBE (2 à 4 fois l'EBE retraité est l'usage), prix / chiffre d'affaires, poids du loyer |
| Enchères | décote du prix d'adjudication **probable** (mise à prix × médiane observée des adjudications par type, 2,06 pour un appartement, 1,46 pour une maison) |

Les montants (loyer, rendement, CA, EBE, taxe foncière, charges, travaux) sont lus
dans les champs structurés puis dans le texte (`src/immo/finance.py`). Un loyer ou un
rendement « potentiel », « estimé », en colocation ou après travaux n'est jamais
pris pour un loyer encaissé. Les résidences gérées (étudiantes, seniors, LMNP) sont
exclues du classement locatif. Toutes les hypothèses se règlent dans `.env`
(préfixe `INVEST_`, voir `.env.example`) et le détail de chaque annonce propose un
simulateur de financement modifiable.

## Sources

| Famille | Sources actives |
|---|---|
| Portails et réseaux résidentiels | Bien'ici (API publique, ~950 000 annonces), iad, Orpi, Laforêt, Safti, Century 21, ERA, Foncia, Citya, Propriétés-Privées, EntreParticuliers |
| Notaires | notaires.fr (API, Crawl-delay 10 s respecté), Immonot |
| Immobilier professionnel et fonds | Bpifrance Transmission, BureauxLocaux, Michel Simond, Place des Commerces, Arthur Loyd, CBRE, MursCommerciaux, PointDeVente, GeoLocaux |
| Enchères et cessions publiques | Licitor, Avoventes, Enchères Immobilières, Agorastore, 36h immo |

Figaro Immobilier et Vench ont un connecteur mais restent hors de `SYNC_SOURCES` :
Figaro présente actuellement un challenge Cloudflare, Vench réserve ses résultats aux
abonnés et recoupe Licitor. PAP, SeLoger, Logic-Immo, Efficity et Superimmo bloquent
toute collecte automatisée. L'audit est dans `config/source-audit.md`.

**Leboncoin** reste désactivé : DataDome bloque le navigateur automatisé dès la
première page (trois essais en juillet 2026). Aucun contournement n'est implémenté.

## Disponibilité, prix et doublons

- Les connecteurs par sitemap enregistrent à chaque passage l'inventaire complet des
  URL publiées ; une annonce absente du dernier inventaire complet est retirée.
  Les autres sources sont retirées après `LISTING_STALE_DAYS` sans observation.
- Chaque changement de prix est historisé (`prix_historique`) : baisses affichées,
  filtrables et triables.
- Une annonce publiée sur plusieurs plateformes (même type, code postal, surface
  arrondie, prix à moins de 3 %) n'apparaît qu'une fois ; le détail liste les autres
  publications et leurs prix.

## Tableau de bord local

```bash
source .venv/bin/activate
export PYTHONPATH=src
python -m immo.cli web
```

Ouvrir `http://127.0.0.1:8000`. Onglets par stratégie, filtres d'investissement
(rendement net minimal, cash-flow positif, loyer réel publié, baisse de prix,
nouveautés, enchères), tri par score, rendement, cash-flow, décote ou baisse.
`python -m immo.cli web-install` le garde actif à chaque ouverture de session macOS.

## Fonctionnement automatique

Une synchronisation : actualisation DVF et ANIL si nécessaire, collecte parallèle de
`SYNC_SOURCES` (`SYNC_MAX_WORKERS` sources à la fois, chacune bornée en temps et en
volume par cycle), normalisation, inventaire, historique des prix, extraction des
montants, références DVF et loyers, puis recalcul de `deal_analysis`. Une
notification macOS signale les nouvelles affaires au-dessus de `NOTIFY_MIN_SCORE`.

```bash
python -m immo.cli schedule --every-hours 6   # une seule fois
python -m immo.cli sync                       # passage immédiat
python -m immo.cli deals --limit 20           # meilleures affaires en ligne
python -m immo.cli compact                    # récupérer l'espace disque de DuckDB
```

Les journaux sont dans `data/scheduler.out.log` et `data/scheduler.err.log`. Un
verrou empêche deux synchronisations simultanées.

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

## Exécution complète

Étapes manuelles, dans l'ordre :

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
