from easynews_indexer.categories import MOVIES_UHD
from easynews_indexer.mapping import map_results


def row(title="Movie 2025 2160p REMUX", size=2_000_000_000):
    return {"hash":"h1","fn":title,"extension":".mkv","size":size,"runtime":7200,"timestamp":1700000000}


def test_mapping_filters_category_and_size():
    data = {"data": [row(), row("Tiny 2025 2160p", 10)]}
    items = map_results(data, 100_000_000, "Movie", year=2025, requested_categories={MOVIES_UHD})
    assert len(items) == 1
    assert items[0]["category"] == MOVIES_UHD


def test_parent_query_tokens_filter_noise():
    data = {"data": [row("Movie 2025 2160p REMUX"), {**row("Other 2025 2160p"), "hash":"h2"}]}
    items = map_results(data, 1, "Movie", year=2025)
    assert [x["hash"] for x in items] == ["h1"]


def test_sample_and_trailer_suffixes_are_rejected():
    data = {"data": [
        row("Movie.2025.2160p.WEB-DL-sample"),
        {**row("Movie.2025.2160p.WEB-DL-trailer"), "hash":"h2"},
        {**row("Movie.2025.2160p.WEB-DL"), "hash":"h3"},
    ]}
    items = map_results(data, 1, "Movie", year=2025)
    assert [x["hash"] for x in items] == ["h3"]


def test_sample_word_inside_real_title_is_not_rejected():
    data = {"data": [row("The.Sample.2025.2160p.WEB-DL")]}
    items = map_results(data, 1, "The Sample", year=2025)
    assert [x["hash"] for x in items] == ["h1"]
