from physical_context.config import Settings
from physical_context.database import Database


def initialize_storage(settings: Settings) -> Database:
    settings.captures_dir.mkdir(parents=True, exist_ok=True)

    database = Database(settings.database_path)
    database.migrate()
    return database
