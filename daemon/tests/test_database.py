from pathlib import Path

from physical_context.database import Database


def test_migrations_create_schema_and_are_repeatable(tmp_path: Path) -> None:
    database = Database(tmp_path / "physical_context.db")

    database.migrate()
    database.migrate()

    with database.connect() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        schema_objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')"
            )
        }
        capture_columns = {row[1] for row in connection.execute("PRAGMA table_info(captures)")}
        vec_version = connection.execute("SELECT vec_version()").fetchone()[0]

    assert version == 1
    assert {
        "captures",
        "captures_fts",
        "captures_vec",
        "captures_fts_after_insert",
        "captures_fts_after_delete",
        "captures_fts_after_update",
    } <= schema_objects
    assert {
        "id",
        "client_capture_id",
        "created_at",
        "device_ts",
        "image_path",
        "caption",
        "tags",
        "hostname",
        "git_repo",
        "git_branch",
        "git_sha",
        "sharpness",
        "state",
    } == capture_columns
    assert vec_version
