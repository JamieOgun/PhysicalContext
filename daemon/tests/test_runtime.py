from pathlib import Path

from physical_context.config import Settings
from physical_context.database import Database
from physical_context.runtime import initialize_storage


def test_initialize_storage_creates_capture_dir_and_database(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path, _env_file=None)

    database = initialize_storage(settings)

    assert settings.captures_dir.is_dir()
    assert settings.database_path.is_file()
    assert isinstance(database, Database)

    with database.connect() as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
