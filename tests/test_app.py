from dataclasses import replace
from easynews_indexer.app import create_app
from easynews_indexer.config import Settings


class StubService:
    def __init__(self, ready=True):
        self.ready = ready
        self.validate_calls = 0

    def validate(self):
        self.validate_calls += 1
        self.ready = True

    def search(self, **kwargs):
        return [{"hash":"h","filename":"Movie","ext":".mkv","size":123456789,"title":"Movie.2025.2160p.REMUX.mkv","category":2045,"year":2025,"quality":"2160p","posted":1700000000}]


def settings():
    s = Settings.from_env()
    return replace(s, easynews_user="u", easynews_pass="p", api_key="secret", signing_secret="sign")


def test_caps_and_newznab_response(monkeypatch):
    app = create_app(settings(), StubService())
    client = app.test_client()
    caps = client.get("/api?t=caps&apikey=secret")
    assert caps.status_code == 200
    assert b'id="2045" name="Movies/UHD"' in caps.data
    resp = client.get("/api?t=movie&q=Movie&year=2025&apikey=secret")
    assert resp.status_code == 200
    assert b'<newznab:response offset="0" total="1"/>' in resp.data
    assert b'<category>2045</category>' in resp.data


def test_health_does_not_require_api_key():
    app = create_app(settings(), StubService())
    assert app.test_client().get("/healthz").status_code == 200


def test_deep_readyz_validates_upstream():
    service = StubService(ready=False)
    app = create_app(settings(), service)
    client = app.test_client()

    shallow = client.get("/readyz")
    assert shallow.status_code == 200
    assert shallow.get_json() == {"status": "configured", "upstream_validated": False}
    assert service.validate_calls == 0

    deep = client.get("/readyz?deep=1&apikey=secret")
    assert deep.status_code == 200
    assert deep.get_json() == {"status": "ready", "upstream_validated": True}
    assert service.validate_calls == 1


def test_deep_readyz_requires_api_key():
    service = StubService(ready=False)
    app = create_app(settings(), service)
    response = app.test_client().get("/readyz?deep=1")
    assert response.status_code == 401
    assert service.validate_calls == 0