from easynews_indexer import categories as c
from easynews_indexer.release import parse_release


def test_prowlarr_standard_category_ids():
    assert c.MOVIES_SD == 2030
    assert c.MOVIES_HD == 2040
    assert c.MOVIES_UHD == 2045
    assert c.TV_SD == 5030
    assert c.TV_HD == 5040
    assert c.TV_UHD == 5045


def test_release_categories():
    assert parse_release("Movie.2025.2160p.REMUX.HEVC.mkv").category == c.MOVIES_UHD
    assert parse_release("Show.S02E03.1080p.WEB-DL.x265.mkv").category == c.TV_HD


def test_parent_category_matches_children():
    assert c.category_matches(c.MOVIES_UHD, {c.MOVIES})
    assert c.category_matches(c.TV_HD, {c.TV})
