from pathlib import Path

from fastapi.testclient import TestClient

from app import main as main_module
from tests.fixtures import make_settings


def test_health_endpoint(tmp_path: Path, monkeypatch) -> None:
    # Importing the app module does not touch the database (lifespan creates
    # it lazily), but the lifespan bootstraps the DB from the module-level
    # settings. Point those at an isolated temp DB so the health probe never
    # reads or writes the formal development database.
    monkeypatch.setattr(main_module, "settings", make_settings(tmp_path))
    with TestClient(main_module.app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"

