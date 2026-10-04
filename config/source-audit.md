# Audit des sources immobilières — 14 juillet 2026

Le classement ci-dessous est une priorité produit fondée sur l'audience publique,
le volume résidentiel annoncé et la complémentarité des catalogues. Ce n'est pas
un relevé Médiamétrie exhaustif. Les tests utilisent une requête HTTP standard,
sans contournement de captcha ni de protection.

| Priorité | Source | Résultat du test | Décision |
|---:|---|---|---|
| 1 | Leboncoin Immobilier | DataDome / captcha | En attente d'un accès autorisé |
| 2 | SeLoger | HTTP 403 | Non automatisé |
| 3 | Bien'ici | Recherche accessible, détails interdits par `robots.txt` | Non automatisé |
| 4 | PAP | HTTP 403 | Non automatisé |
| 5 | Logic-Immo | HTTP 403 | Non automatisé |
| 6 | Superimmo | HTTP 503 répété sur la pagination | Connecteur désactivé |
| 7 | Orpi | Sitemaps nationaux et données d'annonces structurées | **Connecteur actif** |
| 8 | Laforêt | 29 000+ produits dans le sitemap, données structurées | **Connecteur actif** |
| 9 | Figaro Immobilier | HTTP 403 | Non automatisé |
| 10 | ParuVendu | Pages de catégories accessibles, contraintes robots sur plusieurs routes | À approfondir sans routes interdites |

Sources complémentaires déjà présentes : Immonot pour le résidentiel notarial,
PointDeVente et Geolocaux pour l'immobilier professionnel. Century 21 et
Ouest-France Immo ont également été testés mais n'offrent pas actuellement un
parcours national stable et accessible depuis le collecteur local.

## Nouvelle source prioritaire

| Source | Catalogue public observé | Données | Décision |
|---|---:|---|---|
| iad France | 64 723 maisons, 32 651 appartements, 9 592 terrains, 5 228 commerces et 2 415 immeubles | Sitemap officiel quotidien et JSON-LD complet | **Connecteur actif, 15 000 candidates/cycle** |

Le sitemap PAP annonce bien un catalogue national de particuliers, mais son
téléchargement direct renvoie actuellement une vérification Cloudflare. Il reste
donc prioritaire si un accès public stable apparaît, sans tentative de
contournement. Capifrance et Optimhome présentent le même blocage. Les chemins
d'annonces Century 21 et Bien'ici ne sont pas retenus car leurs fichiers
`robots.txt` les excluent explicitement.

## Champs validés

- iad France : type, prix, surface bâtie/terrain, pièces, chambres, commune,
  code postal, description, photo, annonceur et URL ;
- Orpi : les champs précédents plus coordonnées, DPE, GES, agence et date de
  publication lorsque disponibles ;
- Laforêt : type, prix, surface, pièces, commune, code postal, description et URL.
