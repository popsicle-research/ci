from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from popsicle.storage.sqlite import SQLiteStore
from popsicle.webui import register_ui


@pytest.fixture
def app_and_store(tmp_path: Path) -> tuple[Flask, SQLiteStore]:
    db_path = tmp_path / "test.db"
    store = SQLiteStore(db_path)
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_ui(app, store)
    return app, store
