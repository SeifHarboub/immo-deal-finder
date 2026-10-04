from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

from immo.reference.dvf import refresh
from immo.runner import collect, normalize, score
from immo.schema import Criteria
from immo.warehouse import connect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LABEL = "fr.local.immo-deal-finder"
WEB_LABEL = "fr.local.immo-deal-finder.web"


def _reference_is_stale(max_age_days: int) -> bool:
    marker = PROJECT_ROOT / "data" / "dvf.last_refresh"
    if not marker.exists():
        return True
    modified = datetime.fromtimestamp(marker.stat().st_mtime, timezone.utc)
    return modified < datetime.now(timezone.utc) - timedelta(days=max_age_days)


def _rent_reference_is_stale(max_age_days: int) -> bool:
    marker = PROJECT_ROOT / "data" / "rents.last_refresh"
    if not marker.exists():
        return True
    modified = datetime.fromtimestamp(marker.stat().st_mtime, timezone.utc)
    return modified < datetime.now(timezone.utc) - timedelta(days=max_age_days)


def sync_once() -> dict[str, int]:
    """Exécute toute la chaîne. Un verrou empêche deux synchronisations simultanées."""
    lock_path = PROJECT_ROOT / "data" / "sync.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Une synchronisation est déjà en cours : ce lancement est ignoré.")
        lock.close()
        return {"collected": 0, "failed_searches": 0}

    sync_started = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        # Un arrêt système peut laisser un run marqué « running ». Au démarrage
        # suivant, il ne peut plus être actif puisque le verrou vient d'être acquis.
        with connect() as con:
            con.execute("""
                UPDATE collection_runs
                SET status='interrupted', finished_at=current_timestamp,
                    error=COALESCE(error, 'Processus précédent interrompu')
                WHERE status='running'
            """)
        reference_days = int(os.getenv("DVF_REFRESH_DAYS", "30"))
        if _reference_is_stale(reference_days):
            print("Actualisation du référentiel DVF…", flush=True)
            refresh()
            (PROJECT_ROOT / "data" / "dvf.last_refresh").touch()
            # Les voisins sont dépendants du millésime DVF : une nouvelle base
            # invalide puis reconstruit toutes les références d'annonces.
            from immo.comparables import refresh_comparables
            with connect() as con:
                con.execute("DELETE FROM annonce_reference")
                refresh_comparables(con)

        rent_days = int(os.getenv("RENTS_REFRESH_DAYS", "30"))
        if _rent_reference_is_stale(rent_days):
            print("Actualisation du référentiel ANIL des loyers…", flush=True)
            from immo.reference.rents import refresh as refresh_rents
            count = refresh_rents()
            from immo.rent_estimates import refresh_rent_estimates
            with connect() as con:
                con.execute("DELETE FROM annonce_loyer_reference")
                refresh_rent_estimates(con)
            (PROJECT_ROOT / "data" / "rents.last_refresh").touch()
            print(f"Référentiel loyers prêt : {count} indicateurs.", flush=True)

        total = 0
        failures = 0
        sources = [value.strip() for value in os.getenv(
            "SYNC_SOURCES", "pointdevente,immonot,geolocaux"
        ).split(",") if value.strip()]

        # Les lectures réseau sont parallèles. `warehouse.connect` sérialise les
        # courtes écritures DuckDB au sein du processus.
        from immo.connectors import discover_connectors
        discover_connectors()

        def collect_source(source: str) -> tuple[str, int, Exception | None, float]:
            print(f"Collecte {source}…", flush=True)
            started = time.monotonic()
            try:
                count = collect(source, Criteria())
                return source, count, None, time.monotonic() - started
            except Exception as exc:
                print(f"Source en échec, poursuite du lot : {exc}", flush=True)
                return source, 0, exc, time.monotonic() - started

        workers = max(1, min(len(sources), int(os.getenv("SYNC_MAX_WORKERS", "4"))))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="collector") as pool:
            futures = [pool.submit(collect_source, source) for source in sources]
            for future in as_completed(futures):
                source, count, error, elapsed = future.result()
                total += count
                failures += int(error is not None)
                rate = count / elapsed * 60 if elapsed and count else 0
                print(
                    f"Collecte {source} terminée : {count} annonces en {elapsed:.1f}s "
                    f"({rate:.0f}/min).", flush=True,
                )

        # Phase 2 : une seule normalisation par source. Les téléchargements sont
        # tous terminés, donc les calculs DuckDB ne bloquent plus les autres
        # connecteurs et chaque table Bronze n'est parcourue qu'une fois.
        for source in sources:
            started = time.monotonic()
            try:
                print(f"Normalisation {source}…", flush=True)
                normalize(source)
                print(
                    f"Normalisation {source} terminée en "
                    f"{time.monotonic() - started:.1f}s.", flush=True,
                )
            except Exception as exc:
                failures += 1
                print(f"Normalisation {source} en échec : {exc}", flush=True)
        with connect() as con:
            has_reference = con.execute("SELECT count(*) FROM prix_reference").fetchone()[0] > 0
            has_annonces = con.execute("SELECT count(*) FROM annonces_stg").fetchone()[0] > 0
        if has_annonces and has_reference:
            score()
            notify_new_deals(sync_started)
        print(f"Synchronisation terminée : {total} brutes, {failures} recherches en échec.")
        return {"collected": total, "failed_searches": failures}
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def compact_database() -> tuple[int, int]:
    """Réécrit la base : DuckDB ne rend jamais l'espace des lignes supprimées."""
    lock_path = PROJECT_ROOT / "data" / "sync.lock"
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Synchronisation en cours : compactage reporté.")
    try:
        import duckdb
        source = Path(os.environ["IMMO_DB"]).expanduser().resolve()
        target = source.with_name(source.stem + ".compact.duckdb")
        target.unlink(missing_ok=True)
        before = source.stat().st_size
        con = duckdb.connect()
        try:
            con.execute(f"ATTACH '{str(source).replace(chr(39), chr(39) * 2)}' AS old_db (READ_ONLY)")
            con.execute(f"ATTACH '{str(target).replace(chr(39), chr(39) * 2)}' AS new_db")
            con.execute("COPY FROM DATABASE old_db TO new_db")
        finally:
            con.close()
        # Remplacement atomique : le serveur web rouvre la nouvelle base à la requête suivante.
        target.replace(source)
        return before, source.stat().st_size
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def notify_new_deals(since: datetime) -> int:
    """Notification macOS pour les nouvelles affaires apparues pendant la synchronisation."""
    minimum = float(os.getenv("NOTIFY_MIN_SCORE", "75"))
    with connect() as con:
        from immo.searches import NOUVELLE, pending_alerts
        alerts = pending_alerts(con)
        if alerts:
            # Des recherches sauvegardées existent : elles remplacent l'alerte globale.
            for alert in alerts:
                rows = alert["annonces"]
                nouvelles = sum(1 for row in rows if row[7])
                baisses = len(rows) - nouvelles
                parts = [f"{nouvelles} nouvelle{'s' if nouvelles > 1 else ''}"] if nouvelles else []
                if baisses:
                    parts.append(f"{baisses} baisse{'s' if baisses > 1 else ''} de prix")
                best = rows[0]
                price = f"{int(best[4] or 0):,}".replace(",", " ")
                _notify(alert["nom"], " · ".join(parts), f"{best[3] or '?'}, {price} € (score {int(best[5] or 0)})")
            return sum(len(alert["annonces"]) for alert in alerts)
        rows = con.execute(f"""
            SELECT strategie, ville, prix, round(score_global)
            FROM deal_analysis
            WHERE active IS NOT false AND rang_doublon = 1
              AND score_global >= ? AND {NOUVELLE.format(since="?")}
            ORDER BY score_global DESC
        """, [minimum, since]).fetchall()
    if not rows:
        return 0
    labels = {"decote": "sous le marché", "rendement": "locatif", "murs": "murs", "fonds": "fonds"}
    best = rows[0]
    price = f"{int(best[2] or 0):,}".replace(",", " ")
    message = f"Meilleure : {labels.get(best[0], best[0])} à {best[1] or '?'}, {price} € (score {int(best[3])})"
    title = f"{len(rows)} nouvelle{'s' if len(rows) > 1 else ''} affaire{'s' if len(rows) > 1 else ''}"
    _notify("Immo Radar", title, message)
    return len(rows)


def _notify(title: str, subtitle: str, message: str) -> None:
    print(f"{title} — {subtitle}. {message}", flush=True)
    if sys.platform == "darwin" and os.getenv("NOTIFY_MACOS", "true").lower() in {"1", "true", "yes"}:
        script = (f"display notification {json.dumps(message)} with title {json.dumps(title)} "
                  f"subtitle {json.dumps(subtitle)}")
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def install_schedule(every_hours: int = 6) -> Path:
    if every_hours < 1:
        raise ValueError("L'intervalle doit être d'au moins une heure")
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        # Prevent macOS idle sleep for the duration of the synchronization.
        # The display may still turn off; only the Python/network work is kept alive.
        "ProgramArguments": [
            "/usr/bin/caffeinate", "-i",
            sys.executable, "-m", "immo.cli", "sync",
        ],
        "WorkingDirectory": str(PROJECT_ROOT),
        "EnvironmentVariables": {"PYTHONPATH": str(PROJECT_ROOT / "src")},
        "StartInterval": every_hours * 3600,
        "RunAtLoad": False,
        "StandardOutPath": str(data_dir / "scheduler.out.log"),
        "StandardErrorPath": str(data_dir / "scheduler.err.log"),
        "ProcessType": "Background",
    }
    path.write_bytes(plistlib.dumps(payload, sort_keys=False))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    return path


def remove_schedule() -> None:
    path = plist_path()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
    path.unlink(missing_ok=True)


def web_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{WEB_LABEL}.plist"


def install_web_service() -> Path:
    path = web_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data_dir = PROJECT_ROOT / "data"
    payload = {
        "Label": WEB_LABEL,
        "ProgramArguments": [sys.executable, "-m", "immo.cli", "web"],
        "WorkingDirectory": str(PROJECT_ROOT),
        "EnvironmentVariables": {"PYTHONPATH": str(PROJECT_ROOT / "src")},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(data_dir / "web.out.log"),
        "StandardErrorPath": str(data_dir / "web.err.log"),
        "ProcessType": "Background",
    }
    path.write_bytes(plistlib.dumps(payload, sort_keys=False))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    return path


def remove_web_service() -> None:
    path = web_plist_path()
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True)
    path.unlink(missing_ok=True)
