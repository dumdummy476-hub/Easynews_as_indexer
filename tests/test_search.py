from dataclasses import replace
from easynews_indexer.config import Settings
from easynews_indexer.search import SearchService


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.queries = []

    def search(self, **kwargs):
        self.calls += 1
        self.queries.append(kwargs.get("query"))
        return {
            "page": 1,
            "numPages": 1,
            "data": [
                {
                    "hash": "h",
                    "fn": "Movie 2025 2160p REMUX",
                    "extension": ".mkv",
                    "size": 2_000_000_000,
                    "runtime": 7000,
                }
            ],
        }


class HighVolumeMovieClient:
    def __init__(self):
        self.calls = 0
        self.queries = []

    def search(self, **kwargs):
        self.calls += 1
        query = kwargs.get("query")
        self.queries.append(query)

        if query == "Parasite 2019":
            rows = [
                {
                    "hash": f"h{i}",
                    "fn": f"Parasite.2019.1080p.WEB-DL-G{i}",
                    "extension": ".mkv",
                    "size": 2_000_000_000 + i,
                    "runtime": 7900,
                }
                for i in range(25)
            ]
        elif query == "Parasite":
            rows = [
                {
                    "hash": "extra",
                    "fn": "Parasite.2019.2160p.BluRay.REMUX-EXTRA",
                    "extension": ".mkv",
                    "size": 50_000_000_000,
                    "runtime": 7900,
                },
                {
                    "hash": "h0",
                    "fn": "Parasite.2019.1080p.WEB-DL-G0",
                    "extension": ".mkv",
                    "size": 2_000_000_000,
                    "runtime": 7900,
                },
            ]
        else:
            rows = []

        return {"page": 1, "numPages": 1, "data": rows}


def test_search_cache_prevents_repeat_upstream_call():
    settings = replace(
        Settings.from_env(),
        easynews_user="u",
        easynews_pass="p",
        api_key="secret",
        signing_secret="sign",
        search_cache_ttl=60,
        title_retry_trigger=0,
        tmdb_trigger=0,
        posted_nzb_trigger=0,
    )
    fake = FakeClient()
    service = SearchService(settings, fake)
    first = service.search(kind="movie", query="Movie", year=2025, strict=False)
    second = service.search(kind="movie", query="Movie", year=2025, strict=False)
    assert first == second
    assert fake.calls == 2
    assert fake.queries == ["Movie 2025", "Movie"]


def test_movie_title_expansion_runs_even_when_primary_result_count_is_high():
    settings = replace(
        Settings.from_env(),
        easynews_user="u",
        easynews_pass="p",
        api_key="secret",
        signing_secret="sign",
        title_retry_trigger=1,
        tmdb_trigger=0,
        posted_nzb_trigger=0,
    )
    fake = HighVolumeMovieClient()
    service = SearchService(settings, fake)

    results = service.search(kind="movie", query="Parasite", year=2019, strict=False)

    assert fake.queries == ["Parasite 2019", "Parasite"]
    assert len(results) == 26
    assert sum(1 for item in results if item["hash"] == "h0") == 1
    assert any(item["hash"] == "extra" for item in results)
