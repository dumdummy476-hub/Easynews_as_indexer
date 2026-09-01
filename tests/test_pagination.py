from dataclasses import replace
from easynews_indexer.client import EasynewsClient
from easynews_indexer.config import Settings


def test_v3_parallel_pagination_preserves_order_and_dedup(monkeypatch):
    settings = replace(Settings.from_env(), easynews_user="u", easynews_pass="p", v3_max_pages=3, v3_concurrency=2)
    client = EasynewsClient("u", "p", settings)
    pages = {
        1: {"page":1,"numPages":3,"data":[{"hash":"a"},{"hash":"b"}]},
        2: {"page":2,"numPages":3,"data":[{"hash":"b"},{"hash":"c"}]},
        3: {"page":3,"numPages":3,"data":[{"hash":"d"}]},
    }
    monkeypatch.setattr(client, "_search_v3_page", lambda query, page=1, *args, **kwargs: pages[page])
    result = client._search_v3("q", per_page=250)
    assert [x["hash"] for x in result["data"]] == ["a", "b", "c", "d"]
