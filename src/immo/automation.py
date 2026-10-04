from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
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
        print(f"Synchronisation terminée : {total} brutes, {failures} recherches en échec.")
        return {"collected": total, "failed_searches": failures}
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


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
