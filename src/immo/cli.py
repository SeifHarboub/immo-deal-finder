import argparse
import os

from dotenv import load_dotenv


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="immo", description="Détecteur local d'affaires immobilières")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("reference", help="Télécharger et agréger les références DVF")
    commands.add_parser("rents", help="Télécharger le référentiel ANIL des loyers")
    collect = commands.add_parser("collect", help="Collecter des annonces dans la couche bronze")
    collect.add_argument("--source", required=True)
    collect.add_argument("--url", dest="search_url")
    collect.add_argument("--url-file", help="Une URL de recherche partitionnée par ligne")
    collect.add_argument("--zones", nargs="*", default=[])
    collect.add_argument("--prix-max", type=float)
    collect.add_argument("--categorie", choices=("vente", "location"), default="vente")
    collect.add_argument("--max-pages", type=int)
    automatic = commands.add_parser("collect-auto", help="Collecte Leboncoin sans fournir d'URL")
    automatic.add_argument(
        "--departements", nargs="+",
        help="Codes à collecter, ou 'all'. Par défaut : DVF_DEPARTEMENTS",
    )
    automatic.add_argument(
        "--tranches-prix",
        help="Exemple : 0-200000,200001-400000,400001-max",
    )
    automatic.add_argument("--max-pages", type=int)
    normalize = commands.add_parser("normalize", help="Normaliser une source")
    normalize.add_argument("--source", required=True)
    commands.add_parser("score", help="Calculer et classer les affaires")
    deals = commands.add_parser("deals", help="Afficher les meilleures affaires")
    deals.add_argument("--limit", type=int, default=20)
    commands.add_parser("stats", help="Afficher volumétrie, couverture et derniers runs")
    sector = commands.add_parser("secteur", help="Afficher prix et dernières ventes DVF d'un secteur")
    sector.add_argument("--cp", required=True)
    sector.add_argument("--type", choices=("Appartement", "Maison"))
    sector.add_argument("--limit", type=int, default=10)
    commands.add_parser("sync", help="DVF + collecte automatique + normalisation + scoring")
    schedule = commands.add_parser("schedule", help="Activer la synchronisation périodique macOS")
    schedule.add_argument("--every-hours", type=int, default=6)
    commands.add_parser("unschedule", help="Désactiver la synchronisation périodique")
    commands.add_parser("schema-report", help="Auditer les champs réellement récupérés")
    commands.add_parser("compact", help="Réécrire la base pour récupérer l'espace disque")
    commands.add_parser("publish", help="Publier le site en ligne chiffré (GitHub Pages)")
    web = commands.add_parser("web", help="Lancer le tableau de bord local")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    commands.add_parser("web-install", help="Démarrer le tableau de bord automatiquement avec macOS")
    commands.add_parser("web-uninstall", help="Désactiver le serveur web automatique")
    return root


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = parser().parse_args(argv)
    if args.command == "reference":
        from immo.reference.dvf import refresh
        refresh()
        from immo.comparables import refresh_comparables
        from immo.warehouse import connect
        with connect() as con:
            from immo.comparables import ensure_price_index
            ensure_price_index(con, rebuild=True)
            con.execute("DELETE FROM annonce_reference")
            refresh_comparables(con)
        print("Référentiel DVF actualisé.")
    elif args.command == "rents":
        from immo.reference.rents import refresh
        count = refresh()
        print(f"Référentiel ANIL actualisé : {count} indicateurs communaux.")
    elif args.command == "collect":
        from immo.runner import collect
        from immo.schema import Criteria
        urls = [args.search_url]
        if args.url_file:
            with open(args.url_file, encoding="utf-8") as stream:
                urls = [line.strip() for line in stream if line.strip() and not line.lstrip().startswith("#")]
        if not urls:
            urls = [None]
        total = 0
        for position, url in enumerate(urls, 1):
            criteria = Criteria(
                categorie=args.categorie, zones=args.zones, prix_max=args.prix_max,
                search_url=url, max_pages=args.max_pages,
            )
            count = collect(args.source, criteria)
            total += count
            print(f"Recherche {position}/{len(urls)} : {count} annonces collectées.")
        print(f"Total : {total} annonces brutes collectées.")
    elif args.command == "normalize":
        from immo.runner import normalize
        normalize(args.source)
        print(f"Source {args.source} normalisée.")
    elif args.command == "collect-auto":
        from immo.runner import collect, normalize, score
        from immo.schema import Criteria
        from immo.search_plan import leboncoin_search_urls
        departments = args.departements or [
            value.strip() for value in os.environ.get("DVF_DEPARTEMENTS", "93").split(",") if value.strip()
        ]
        urls = leboncoin_search_urls(departments, args.tranches_prix)
        total = 0
        for position, url in enumerate(urls, 1):
            count = collect("leboncoin", Criteria(search_url=url, max_pages=args.max_pages))
            total += count
            print(f"Recherche automatique {position}/{len(urls)} : {count} annonces.")
        normalize("leboncoin")
        from immo.warehouse import connect
        with connect() as con:
            has_reference = con.execute("SELECT count(*) FROM prix_reference").fetchone()[0] > 0
        if has_reference:
            score()
            print("Normalisation et scoring DVF terminés.")
        else:
            print("Normalisation terminée. Lancez 'reference' avant le scoring DVF.")
        print(f"Total automatique : {total} annonces brutes.")
    elif args.command == "score":
        from immo.runner import score
        score()
        print("Scoring terminé.")
    elif args.command == "deals":
        from immo.runner import deals
        deals(args.limit)
    elif args.command == "stats":
        from immo.runner import stats
        stats()
    elif args.command == "secteur":
        from immo.runner import sector
        sector(args.cp, args.type, args.limit)
    elif args.command == "sync":
        from immo.automation import sync_once
        sync_once()
    elif args.command == "schedule":
        from immo.automation import install_schedule
        path = install_schedule(args.every_hours)
        print(f"Synchronisation automatique activée toutes les {args.every_hours} h : {path}")
    elif args.command == "unschedule":
        from immo.automation import remove_schedule
        remove_schedule()
        print("Synchronisation automatique désactivée.")
    elif args.command == "publish":
        from immo.publish import publish
        publish()
    elif args.command == "compact":
        from immo.automation import compact_database
        before, after = compact_database()
        print(f"Base compactée : {before / 1e9:.2f} Go → {after / 1e9:.2f} Go.")
    elif args.command == "schema-report":
        from immo.runner import schema_report
        schema_report()
    elif args.command == "web":
        import uvicorn
        uvicorn.run("immo.web.app:app", host=args.host, port=args.port)
    elif args.command == "web-install":
        from immo.automation import install_web_service
        path = install_web_service()
        print(f"Serveur web automatique activé : {path}")
    elif args.command == "web-uninstall":
        from immo.automation import remove_web_service
        remove_web_service()
        print("Serveur web automatique désactivé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
