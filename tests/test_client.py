import xml.etree.ElementTree as ET
from easynews_indexer.client import EasynewsClient, EasynewsError
import pytest


def test_nzb_validation_repairs_empty_date():
    raw = b'<?xml version="1.0"?><nzb><file date=""></file></nzb>'
    fixed = EasynewsClient.validate_nzb_bytes(raw)
    assert b'date="0"' in fixed
    ET.fromstring(fixed)


def test_nzb_validation_rejects_html():
    with pytest.raises(EasynewsError):
        EasynewsClient.validate_nzb_bytes(b"<html></html>")
