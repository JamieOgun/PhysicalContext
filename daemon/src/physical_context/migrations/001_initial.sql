CREATE TABLE captures (
    id TEXT PRIMARY KEY NOT NULL,
    client_capture_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    device_ts INTEGER,
    image_path TEXT NOT NULL,
    caption TEXT,
    tags TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(tags) AND json_type(tags) = 'array'),
    hostname TEXT,
    git_repo TEXT,
    git_branch TEXT,
    git_sha TEXT,
    sharpness REAL,
    state TEXT NOT NULL
);

CREATE INDEX captures_created_at_idx ON captures(created_at DESC);
CREATE INDEX captures_state_idx ON captures(state);

CREATE VIRTUAL TABLE captures_fts USING fts5(
    caption,
    tags,
    content = 'captures',
    content_rowid = 'rowid'
);

CREATE TRIGGER captures_fts_after_insert AFTER INSERT ON captures BEGIN
    INSERT INTO captures_fts(rowid, caption, tags)
    VALUES (new.rowid, coalesce(new.caption, ''), new.tags);
END;

CREATE TRIGGER captures_fts_after_delete AFTER DELETE ON captures BEGIN
    INSERT INTO captures_fts(captures_fts, rowid, caption, tags)
    VALUES ('delete', old.rowid, coalesce(old.caption, ''), old.tags);
END;

CREATE TRIGGER captures_fts_after_update AFTER UPDATE OF caption, tags ON captures BEGIN
    INSERT INTO captures_fts(captures_fts, rowid, caption, tags)
    VALUES ('delete', old.rowid, coalesce(old.caption, ''), old.tags);
    INSERT INTO captures_fts(rowid, caption, tags)
    VALUES (new.rowid, coalesce(new.caption, ''), new.tags);
END;

CREATE VIRTUAL TABLE captures_vec USING vec0(
    capture_id TEXT PRIMARY KEY,
    embedding FLOAT[512]
);
