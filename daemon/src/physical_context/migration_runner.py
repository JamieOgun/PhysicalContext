import sqlite3
from importlib import resources

MIGRATIONS = ("001_initial.sql", "002_quality.sql", "003_vector_cosine.sql")


def apply_migrations(connection: sqlite3.Connection) -> None:
    current_version = connection.execute("PRAGMA user_version").fetchone()[0]
    latest_version = len(MIGRATIONS)

    if current_version > latest_version:
        raise RuntimeError(
            f"Database version {current_version} is newer than supported version {latest_version}"
        )

    migration_root = resources.files("physical_context.migrations")
    for version, filename in enumerate(MIGRATIONS, start=1):
        if version <= current_version:
            continue

        sql = migration_root.joinpath(filename).read_text(encoding="utf-8")
        try:
            connection.executescript(
                f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;"
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
