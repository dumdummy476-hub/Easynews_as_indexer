from dataclasses import replace
import pytest
from easynews_indexer.config import Settings


def test_default_api_key_rejected():
    s = replace(Settings.from_env(), easynews_user="u", easynews_pass="p", api_key="testkey", signing_secret="x")
    with pytest.raises(RuntimeError): s.validate_runtime()
