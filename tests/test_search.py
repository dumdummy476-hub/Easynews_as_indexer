from dataclasses import replace
from easynews_indexer.config import Settings
from easynews_indexer.search import SearchService


class FakeClient:
    def __init__(self): self.calls = 0
    def search(self, **kwargs):
        self.calls += 1
        return {"page":1,"numPages":1,"data":[{"hash":"h","fn":"Movie 2025 2160p REMUX","extension":".mkv","size":2_000_000_000,"runtime":7000}]}


def test_search_cache_prevents_repeat_upstream_call():
    settings = replace(Settings.from_env(), easynews_user="u", easynews_pass="p", api_key="secret", signing_secret="sign", search_cache_ttl=60, title_retry_trigger=0, tmdb_trigger=0, posted_nzb_trigger=0)
    fake = FakeClient()
    service = SearchService(settings, fake)
    first = service.search(kind="movie", query="Movie", year=2025, strict=False)
    second = service.search(kind="movie", query="Movie", year=2025, strict=False)
    assert first == second
    assert fake.calls == 1
