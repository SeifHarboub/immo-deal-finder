from immo.connectors.agency import AgencyJsonLdConnector
from immo.schema import Criteria


class _FastConnector(AgencyJsonLdConnector):
    def __init__(self) -> None:
        super().__init__("fasttest", "https://example.test/sitemap.xml")

    def _sitemap_urls(self, _url, seen=None):
        for index in range(25):
            yield f"https://example.test/property/{index}"

    def _fetch_detail(self, url):
        return "<html></html>", url

    def parse_page(self, _html, page_url):
        yield {"id": page_url.rsplit("/", 1)[-1], "url": page_url}


def test_parallel_fetch_keeps_every_candidate(monkeypatch, capsys):
    monkeypatch.setenv("AGENCY_FETCH_WORKERS_FASTTEST", "4")
    monkeypatch.setenv("AGENCY_FETCH_WORKERS_CAP", "8")
    monkeypatch.setenv("AGENCY_PREFETCH_FACTOR", "3")
    monkeypatch.setenv("AGENCY_MAX_RUNTIME_SECONDS", "60")
    monkeypatch.setattr("immo.warehouse.connect", lambda: (_ for _ in ()).throw(RuntimeError()))

    rows = list(_FastConnector().fetch(Criteria(max_pages=25)))

    assert len(rows) == 25
    assert all(row["_parser_version"] == 4 for row in rows)
    assert "25 requêtes, 25 réponses, 25 annonces" in capsys.readouterr().out


def test_parallel_fetch_opens_circuit_on_total_outage(monkeypatch, capsys):
    connector = _FastConnector()
    monkeypatch.setattr(connector, "_fetch_detail", lambda _url: None)
    monkeypatch.setenv("AGENCY_FETCH_WORKERS_FASTTEST", "2")
    monkeypatch.setenv("AGENCY_PREFETCH_FACTOR", "2")
    monkeypatch.setenv("AGENCY_CIRCUIT_BREAKER_FASTTEST", "2")
    monkeypatch.setattr("immo.warehouse.connect", lambda: (_ for _ in ()).throw(RuntimeError()))

    assert list(connector.fetch(Criteria(max_pages=25))) == []
    output = capsys.readouterr().out
    assert "coupe-circuit ouvert" in output
    assert "25 requêtes" not in output
