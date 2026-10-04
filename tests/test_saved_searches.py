from datetime import datetime, timedelta
from pathlib import Path
import tempfile


def test_recherche_alerte_une_seule_fois(monkeypatch):
    db = Path(tempfile.gettempdir()) / "immo-saved-searches.duckdb"
    db.unlink(missing_ok=True)
    monkeypatch.setenv("IMMO_DB", str(db))
    monkeypatch.setenv("DVF_DB", str(Path(tempfile.gettempdir()) / "absent-dvf.duckdb"))
    from immo.warehouse import connect
    from immo.searches import pending_alerts, save
    from immo.filters import where_from_query
    clause, params = where_from_query("segment=immeuble&yield_min=7&department=59&cashflow_positive=true")
    assert "rendement_net >= ?" in clause and params == ["immeuble", "59%", 7.0]
    with connect() as con:
        # Source connue depuis dix jours : seule l'annonce d'hier est nouvelle.
        con.execute("""
            INSERT INTO annonces_stg (source, external_id, categorie, type_bien, first_seen_at)
            VALUES ('orpi', 'ancienne', 'vente', 'immeuble', now() - INTERVAL 10 DAY),
                   ('orpi', 'recente', 'vente', 'immeuble', now() - INTERVAL 1 DAY);
            CREATE TABLE deal_analysis AS SELECT
                source, external_id, first_seen_at, NULL::TIMESTAMP AS derniere_baisse,
                'Immeuble' AS titre, 'Lille' AS ville, 200000.0 AS prix, 90.0 AS score_global,
                'rendement' AS strategie, 'immeuble' AS segment, '59000' AS code_postal,
                8.5 AS rendement_net, 150.0 AS cashflow_mensuel, true AS active, 1 AS rang_doublon
            FROM annonces_stg;
        """)
        save(con, "Immeubles Nord", "segment=immeuble&yield_min=7&department=59&cashflow_positive=true")
        con.execute("UPDATE recherches_sauvegardees SET derniere_alerte = ?", [datetime.utcnow() - timedelta(days=3)])
        alerts = pending_alerts(con)
        assert [row[1] for row in alerts[0]["annonces"]] == ["recente"]
        assert pending_alerts(con) == []
    db.unlink(missing_ok=True)
