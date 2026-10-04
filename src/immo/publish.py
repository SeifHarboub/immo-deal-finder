"""Publication du tableau de bord en ligne, chiffré, sur GitHub Pages.

Le site en ligne est statique : il ne contient que les meilleures affaires
(bonnes, excellentes et enchères à venir). Les annonces sont compressées puis
chiffrées en AES-256-GCM avec une clé dérivée du mot de passe (PBKDF2-SHA256) ;
le navigateur les déchiffre localement. Sans mot de passe, le dépôt public et
le site ne révèlent aucune annonce.

La branche `gh-pages` est réécrite à chaque publication (un seul commit) pour
que le dépôt ne grossisse pas d'un instantané chiffré à chaque synchronisation.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from immo.warehouse import connect


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SITE_ROOT = Path(__file__).parent / "site"
ITERATIONS = 310_000

COLUMNS = [
    "source", "external_id", "titre", "type_bien", "segment", "strategie", "niveau_affaire",
    "score_global", "ville", "code_postal", "prix", "prix_m2", "surface_bati", "surface_terrain",
    "nb_pieces", "dpe", "decote", "median_eur_m2", "nb_ventes", "confiance", "rendement_net",
    "rendement_brut_retenu", "cashflow_mensuel", "loyer_mensuel_retenu", "loyer_reel",
    "travaux_estimes", "cout_total", "chiffre_affaires", "ebe", "multiple_ebe",
    "vente_encheres", "mode_vente", "date_vente", "prix_compare", "baisse_prix_pct",
    "jours_en_ligne", "nb_publications", "sources_doublon", "risques_evaluation", "url",
]


def _rows() -> list[dict]:
    limit = int(os.getenv("PUBLISH_MAX_LISTINGS", "15000"))
    with connect() as con:
        cursor = con.execute(f"""
            SELECT {", ".join(COLUMNS)},
                   left(regexp_replace(coalesce(description, ''), '\\s+', ' ', 'g'), 420) AS extrait,
                   images_json
            FROM deal_analysis
            WHERE active IS NOT false AND rang_doublon = 1
              AND (niveau_affaire IN ('excellente', 'bonne')
                   OR (vente_encheres AND (date_vente IS NULL OR date_vente >= now())))
            ORDER BY score_global DESC NULLS LAST
            LIMIT ?
        """, [limit])
        names = [column[0] for column in cursor.description]
        rows = [dict(zip(names, row)) for row in cursor.fetchall()]
    for row in rows:
        try:
            images = json.loads(row.pop("images_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            images = []
        first = next((item if isinstance(item, str) else (item or {}).get("url")
                      for item in images if item), None) if isinstance(images, list) else None
        row["image"] = first
        for key, value in list(row.items()):
            if isinstance(value, float):
                row[key] = round(value, 4)
            elif isinstance(value, datetime):
                row[key] = value.isoformat()
            elif value is None or value == "":
                del row[key]
    return rows


def encrypt(payload: bytes, password: str) -> dict:
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(
        password.encode("utf-8")
    )
    ciphertext = AESGCM(key).encrypt(nonce, gzip.compress(payload, mtime=0), None)
    encode = lambda value: base64.b64encode(value).decode("ascii")  # noqa: E731
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iterations": ITERATIONS,
            "salt": encode(salt), "nonce": encode(nonce), "data": encode(ciphertext)}


def build(target: Path, password: str) -> int:
    rows = _rows()
    from immo.analysis import assumptions as invest_assumptions
    assumptions = invest_assumptions()
    with connect() as con:
        total = con.execute(
            "SELECT count(*) FROM deal_analysis WHERE active IS NOT false AND rang_doublon = 1"
        ).fetchone()[0]
    payload = json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_en_ligne": total, "hypotheses": assumptions, "annonces": rows,
    }, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    shutil.copytree(SITE_ROOT, target, dirs_exist_ok=True)
    # Même feuille de style que le tableau de bord local.
    shutil.copy(Path(__file__).parent / "web" / "static" / "styles.css", target / "styles.css")
    (target / "data.json").write_text(json.dumps(encrypt(payload, password)), encoding="utf-8")
    (target / ".nojekyll").write_text("", encoding="utf-8")
    (target / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    return len(rows)


def _git(*args: str, cwd: Path, env: dict | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} a échoué : {result.stderr.strip()[:400]}")
    return result.stdout.strip()


def publish() -> str:
    password = os.getenv("PUBLISH_PASSWORD", "").strip()
    if len(password) < 12:
        raise RuntimeError("PUBLISH_PASSWORD absent ou trop court (12 caractères minimum) dans .env")
    repository = os.getenv("PUBLISH_REPOSITORY", "SeifHarboub/immo-deal-finder")
    account = os.getenv("PUBLISH_GITHUB_ACCOUNT", repository.split("/")[0])
    # Jeton lu au moment de la publication dans le trousseau de `gh`, jamais stocké :
    # le compte actif de `gh` peut rester celui du travail.
    token = subprocess.run(["gh", "auth", "token", "--user", account],
                           capture_output=True, text=True).stdout.strip()
    if not token:
        raise RuntimeError(f"Aucun jeton GitHub pour le compte {account} (gh auth login)")
    with tempfile.TemporaryDirectory(prefix="immo-site-") as directory:
        target = Path(directory)
        count = build(target, password)
        env = {**os.environ, "GIT_AUTHOR_NAME": "Immo Radar", "GIT_COMMITTER_NAME": "Immo Radar",
               "GIT_AUTHOR_EMAIL": "immo-radar@users.noreply.github.com",
               "GIT_COMMITTER_EMAIL": "immo-radar@users.noreply.github.com"}
        _git("init", "-q", "-b", "gh-pages", cwd=target)
        _git("add", "-A", cwd=target)
        _git("commit", "-q", "-m", f"Publication du {datetime.now():%d/%m/%Y %H:%M}", cwd=target, env=env)
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        # Le jeton passe par l'environnement de git, jamais par la ligne de
        # commande (visible dans la liste des processus).
        push_env = {**env, "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
                    "GIT_TERMINAL_PROMPT": "0"}
        _git("push", "--force", "-q", f"https://github.com/{repository}.git", "gh-pages:gh-pages",
             cwd=target, env=push_env)
    owner, name = repository.split("/")
    url = f"https://{owner.lower()}.github.io/{name}/"
    print(f"Site publié : {count} annonces chiffrées → {url}", flush=True)
    return url
