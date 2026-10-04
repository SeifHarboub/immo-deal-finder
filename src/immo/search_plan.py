from urllib.parse import urlencode


def all_department_codes() -> list[str]:
    metropolitan = [f"{number:02d}" for number in range(1, 96) if number != 20]
    return metropolitan[:19] + ["2A", "2B"] + metropolitan[19:] + ["971", "972", "973", "974"]


def parse_price_buckets(value: str | None) -> list[tuple[int | None, int | None]]:
    """Exemple : 0-200000,200001-400000,400001-max."""
    if not value:
        return [(None, None)]
    buckets = []
    for raw in value.split(","):
        low_raw, high_raw = (part.strip().lower() for part in raw.split("-", 1))
        low = None if low_raw in {"", "min"} else int(low_raw)
        high = None if high_raw in {"", "max"} else int(high_raw)
        if low is not None and high is not None and low > high:
            raise ValueError(f"Tranche de prix invalide : {raw}")
        buckets.append((low, high))
    return buckets


def leboncoin_search_urls(
    departments: list[str], price_buckets: str | None = None
) -> list[str]:
    codes = all_department_codes() if [x.lower() for x in departments] == ["all"] else departments
    urls = []
    for department in codes:
        for low, high in parse_price_buckets(price_buckets):
            params = {"category": "9", "locations": f"d_{department.upper()}"}
            if low is not None or high is not None:
                params["price"] = f"{low if low is not None else 'min'}-{high if high is not None else 'max'}"
            urls.append("https://www.leboncoin.fr/recherche?" + urlencode(params))
    return urls

