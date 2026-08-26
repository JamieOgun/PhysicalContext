import sqlite3

from physical_context.config import Settings


def initialize_storage(settings: Settings) -> None:
    settings.captures_dir.mkdir(parents=True, exist_ok=True)

    # Opening SQLite creates the database file without introducing the T-002 schema yet.
    with sqlite3.connect(settings.database_path):
        pass
