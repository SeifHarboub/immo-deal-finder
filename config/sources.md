# Sources

File d'attente des plateformes, par lots :

- Fait : Leboncoin (Playwright, navigateur visible, résolution manuelle du captcha si nécessaire).
- Priorité validée par tests réels : Immonot pour le résidentiel/notarial,
  PointDeVente et Geolocaux pour le commercial.
- Priorité suivante : BureauxLocaux, après déduplication de ses nombreuses URL SEO.
- Écartés du premier lot : PAP, Bien'ici, SeLoger et Logic-Immo (protections ou
  règles robots incompatibles avec la collecte retenue).
- Lot agences à venir : Laforêt, Century 21, Orpi, via le template sitemap + JSON-LD.

Chaque ajout doit rester isolé dans un connecteur `raw_<source>` et un mapping SQL
`stg_<source>.sql`. La collecte est volontairement lente et limitée à quelques pages.
