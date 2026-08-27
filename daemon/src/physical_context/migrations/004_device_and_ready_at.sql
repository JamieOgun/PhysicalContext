-- device_id has been posted by firmware and validated since T-003, but never
-- stored, leaving captures unable to say which camera took them.
ALTER TABLE captures ADD COLUMN device_id TEXT;

-- created_at records when a capture arrived; nothing recorded when it became
-- searchable, so press-to-retrievable latency could not be measured (T-019).
ALTER TABLE captures ADD COLUMN ready_at TEXT;
