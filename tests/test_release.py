from easynews_indexer.release import parse_release


def test_rich_release_parser():
    info = parse_release("Film.2025.2160p.UHD.BluRay.REMUX.DV.HDR10+.HEVC.TrueHD.Atmos-Group.mkv")
    assert info.resolution == "2160p"
    assert info.source == "REMUX"
    assert info.video_codec == "HEVC"
    assert "DV" in info.hdr and "HDR10+" in info.hdr
    assert info.audio == "TrueHD Atmos"
    assert info.quality_score > 70
    assert 2050 in info.extra_categories
    assert 2090 in info.extra_categories


def test_title_number_is_not_forced_to_requested_year():
    assert parse_release("Blade.Runner.2049.2017.1080p.mkv").year == 2017
