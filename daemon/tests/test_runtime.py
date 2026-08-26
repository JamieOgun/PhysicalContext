import sqlite3
from pathlib import Path

from physical_context.config import Settings
from physical_context.runtime import initialize_storage


def test_initialize_storage_creates_capture_dir_and_database(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path, _env_file=None)

    initialize_storage(settings)

    assert settings.captures_dir.is_dir()
    assert settings.database_path.is_file()

    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
