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


def test_sample_suffix_is_preserved_for_feature_length_media():
    data = {"data": [row("Silo.S03E04.2160p.WEB.H265-CAKES-sample")]}
    items = map_results(data, 1, "Silo", season=3, episode=4)
    assert [x["hash"] for x in items] == ["h1"]


def test_large_media_with_59_second_easynews_duration_is_preserved():
    suspicious = {
        **row("silo.s03e04.multi.hdr.2160p.web.h265-higgsboson-sample", 160 * 1024 * 1024),
        "runtime": 59,
    }
    items = map_results({"data": [suspicious]}, 100 * 1024 * 1024, "Silo", season=3, episode=4)
    assert [x["hash"] for x in items] == ["h1"]


def test_genuinely_tiny_short_preview_is_rejected():
    short = {
        **row("Silo.S03E04.720p.WEB.H264-preview", 30 * 1024 * 1024),
        "runtime": 30,
    }
    items = map_results({"data": [short]}, 1, "Silo", season=3, episode=4)
    assert items == []


def test_strict_tv_match_allows_year_between_title_and_episode():
    full_episode = row(
        "Silo.2023.S03E04.Whatever.You.Do.Dont.Go.Home.2160p.ATVP.WEB-DL.DDP5.1.Atmos.HDR10Plus.H.265-ALANSARI87",
        9_234_000_000,
    )
    full_episode["runtime"] = 2866
    items = map_results(
        {"data": [full_episode]},
        100 * 1024 * 1024,
        "Silo S03E04",
        season=3,
        episode=4,
        strict=True,
    )
    assert [x["hash"] for x in items] == ["h1"]
