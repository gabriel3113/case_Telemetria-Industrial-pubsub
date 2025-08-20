from ingest_service.main import app
from fastapi.testclient import TestClient

def test_healthz():
    c = TestClient(app)
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"
