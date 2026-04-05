import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argus.web import server
from starlette.datastructures import UploadFile


def test_config_import_success(monkeypatch):
    captured_updates: dict = {}

    def fake_write_config(updates):
        captured_updates.update(updates)
        return {"restart_required": [], "skipped": []}

    monkeypatch.setattr(server, "_HAS_CONFIG_API", True)
    monkeypatch.setattr(server, "write_config", fake_write_config)
    monkeypatch.setattr(server, "get_config_path", lambda: "/tmp/argus.ini")
    monkeypatch.setattr(
        "argus.config_schema.validate",
        lambda _path: SimpleNamespace(errors=[], warnings=[]),
    )

    upload = UploadFile(
        filename="argus-config.json",
        file=io.BytesIO(b'{"general":{"callsign":"ARGUS-TEST"}}'),
    )
    response = asyncio.run(server.config_import(upload))

    assert response["status"] == "ok"
    assert captured_updates == {"general": {"callsign": "ARGUS-TEST"}}
