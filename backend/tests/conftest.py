import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个用例独立临时库。

    app 包在采集期已导入（单例），直接 patch config/db 的路径属性，
    再对临时库重新执行 seed；各用例内延迟导入的 app.* 共享同一批模块。
    """
    db_file = tmp_path / "meterseal.db"

    import app.config as config
    import app.db as db

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", db_file)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", db_file)

    from app.seed import seed_if_empty

    seed_if_empty()

    import app.main as main

    return TestClient(main.app)
