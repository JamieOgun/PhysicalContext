-- Recreate the vector index with cosine distance.
--
-- Cosine distance is bounded to [0, 2] and independent of vector magnitude, so
-- the retrieval floor that separates a real semantic hit from the nearest of a
-- set of unrelated captures is a fixed number rather than one that drifts with
-- whatever scale the embedding provider happens to return.
--
-- vec0 shadow tables do not survive ALTER TABLE RENAME, so the table is
-- rebuilt in place. Captures keep their captions and stay searchable through
-- FTS5 throughout; the startup backfill re-embeds every `ready` row whose
-- vector this drops.
DROP TABLE captures_vec;

CREATE VIRTUAL TABLE captures_vec USING vec0(
    capture_id TEXT PRIMARY KEY,
    embedding FLOAT[512] distance_metric=cosine
);
