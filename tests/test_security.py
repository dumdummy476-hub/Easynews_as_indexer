import pytest
from easynews_indexer.security import decode_signed, sign_payload


def test_signed_roundtrip():
    token = sign_payload({"hash": "abc", "title": "x"}, "secret")
    assert decode_signed(token, "secret")["hash"] == "abc"


def test_tampering_rejected():
    token = sign_payload({"hash": "abc"}, "secret")
    body, sig = token.split(".")
    with pytest.raises(ValueError):
        decode_signed(body + "x." + sig, "secret")
