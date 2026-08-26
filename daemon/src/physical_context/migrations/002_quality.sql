ALTER TABLE captures ADD COLUMN brightness REAL;
ALTER TABLE captures ADD COLUMN is_blurry INTEGER
    CHECK (is_blurry IS NULL OR is_blurry IN (0, 1));
ALTER TABLE captures ADD COLUMN is_dark INTEGER
    CHECK (is_dark IS NULL OR is_dark IN (0, 1));
