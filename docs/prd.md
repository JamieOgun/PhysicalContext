# PRD: Physical Context Layer (v1)

**Status:** Draft
**Owner:** —
**Target:** v1 prototype
**Hardware:** M5Stack CoreS3 + M5 Button Unit

**Revision note:** v1 scope narrowed — audio/voice annotation is cut entirely (no mic, no recording, no transcription). Annotation is automatic captioning via VLM only, which resolves the former "US-2 — should annotation be automated via computer vision?" open question: yes, and it was already the architecture (F-13 in the prior draft), it just hadn't been written up as a committed story. See §3 US-2 and §5 for the explicit trade.

**Revision note (2):** Retrieval is now hybrid keyword + semantic, not keyword-only. Rationale: `search_captures` only ever sees caption text — the image itself is locked behind `get_image` and never part of retrieval — so the caption is a lossy compression of the image, and pure keyword matching is a second lossy compression on top of that. Semantic search catches paraphrase/synonym queries that don't share vocabulary with the caption (e.g. "looked fried" vs. "discoloration near U3"); FTS5 keyword match is kept alongside it because embeddings are worse at precise reference lookups ("J4" vs "J3"). Implemented via `sqlite-vec` — no new service, same SQLite file. This flips the former "Semantic/vector search" non-goal to in-scope.

---



## 1. Overview & Context



### Problem

When working on hardware at a laptop, capturing physical context requires a phone: pick it up, unlock, shoot, then move the image to the machine where the agent lives (AirDrop, Notion paste, upload). Each transfer breaks flow and the image arrives stripped of its surrounding context — no timestamp linkage, no annotation, not indexed, not retrievable later.

The observed workflow being replaced:

> Take photo on phone → open Claude on phone → realise the work is in Cursor on the laptop → AirDrop the image → paste → re-explain what it shows.



### Hypothesis

If a dedicated bench device can capture an image with a single button press — automatically captioned via computer vision, no manual annotation step — and make it queryable by any local coding agent within seconds, the phone is removed from the loop entirely and physical context becomes cheap enough to capture that it actually gets captured.

### Target persona

**Primary (v1):** robotics/hardware engineer, laptop-based, agent-assisted (Cursor, Claude Code), works at a fixed bench. n=1, single-user validation.
**Secondary (post-validation):** other bench engineers on the same team, explicitly deferred until the n=1 hypothesis holds.

### What this is not

Not a memory/sync product (context following you across machines). Not a flight recorder (always-on ring buffer). Not a voice-notes tool — annotation is fully automatic; the user never speaks to the device.

---



## 2. Goals & Success Metrics



### Primary success criterion

**Zero AirDrops (or equivalent phone→laptop image transfers) for physical-context purposes during regular bench work.**

### Secondary metrics


| Metric              | Target                                                                                   | Why                                                                                                                                               |
| ------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Retrieval hit rate  | ≥ 80% of `search_captures` queries return the capture the user was thinking of, in top 5 | This is the number the hybrid (keyword + semantic) retrieval strategy has to clear. Referenced in R3; formalized here so it's actually trackable. |
| Blurry-capture rate | Logged, no target — informational                                                        | Feeds the R1 decision on whether v1.5 needs a camera swap.                                                                                        |


---



## 3. User Stories



### US-1 — One-press capture

**Given** the device is powered, on WiFi, and the daemon is running
**When** the user single-presses the button unit
**Then** an image is captured, uploaded, and the device screen shows a confirmation with a short capture ID within 2 seconds.

Acceptance:

- [ ] Confirmation persists on the LCD for ≥3 s and shows the capture ID
- [ ] Debounce: presses within 800 ms of each other register once
- [ ] Hands never leave the keyboard except to press the button
- [ ] Button has exactly one gesture (press). No hold/long-press behavior in v1 — removed along with audio recording.



### US-2 — Automatic captioning

**Given** an image has finished uploading to the daemon
**When** the daemon ingests it
**Then** a VLM generates a domain-neutral structured caption (summary, observable details, visible text, spatial relationships, changes from the previous capture, and uncertainties) with no action from the user beyond the original button press, and the row becomes searchable.

Acceptance:

- [ ] Captioning is fully automatic — there is no manual annotation step, no recording, nothing the user does beyond US-1's single press
- [ ] Caption failure never blocks retrieval — the row still reaches `ready` with caption null, indexed on auto-context alone (git repo/branch/sha, timestamp, hostname)
- [ ] `-local-caption` fallback path produces a caption in the same schema (lower quality accepted) when the API path is disabled



### US-3 — Agent retrieval by description

**Given** captures exist in the index
**When** the agent calls `search_captures` with a natural-language query
**Then** it receives matching records as **text only** — capture ID, ISO-8601 timestamp, caption, tags — and no image bytes.

Acceptance:

- [ ] Results ranked by relevance — hybrid of FTS5 keyword match and `sqlite-vec` semantic similarity on the caption embedding, merged into one ranked list — default limit 5
- [ ] Response for 5 results stays under ~1500 tokens
- [ ] Empty result set returns an explicit "no matches" rather than nearest-neighbour noise
- [ ] A row with no embedding (embedding generation failed or is still pending) still surfaces via keyword match alone, never silently dropped from results



### US-4 — Agent retrieval of pixels

**Given** the agent has a capture ID from search
**When** it calls `get_image` with that ID
**Then** it receives the image, downscaled to a max edge of 1024 px.

Acceptance:

- [ ] Only ever one image per call — no bulk image retrieval
- [ ] Invalid ID returns a clear error, not an empty payload



### US-5 — Offline resilience

**Given** WiFi is down or the daemon is unreachable
**When** the user captures
**Then** the capture is queued on the device and the LCD shows a queued-count badge; the queue flushes automatically on reconnect.

Acceptance:

- [ ] Queue survives device reboot (persisted to SD/flash)
- [ ] Queue holds ≥ 20 captures before refusing new ones
- [ ] Queue-full state is visually distinct from normal idle

---



## 4. Functional Requirements



### 4.1 Device firmware (CoreS3)


| ID  | Requirement                                                                                                                                    |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| F-1 | Capture single JPEG stills at max sensor resolution. No video, no audio, in v1.                                                                |
| F-2 | Discover the daemon via mDNS (`_pcl._tcp.local`); fall back to a static IP in NVS config.                                                      |
| F-3 | Upload as `multipart/form-data` POST to `/capture` with fields: `image`, `device_ts`, `device_id`, `client_capture_id` (UUID for idempotency). |
| F-4 | Persist unsent captures to SD card; retry with exponential backoff (2s → 60s cap).                                                             |
| F-5 | LCD states: `IDLE` (+ queue badge), `CAPTURING`, `UPLOADING`, `OK <id>`, `QUEUED (n)`, `ERROR <reason>`.                                       |
| F-6 | Sync device clock via SNTP on boot; include drift estimate in upload metadata.                                                                 |
| F-7 | Touchscreen shows the last 5 captures as thumbnails with captions once returned by the daemon. Read-only.                                      |




### 4.2 Daemon (laptop)


| ID   | Requirement                                                                                                                                                                                                                                                           |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| F-8  | HTTP listener on localhost + LAN interface, port 8787, advertised via mDNS.                                                                                                                                                                                           |
| F-9  | On receipt: write image to `~/.pcl/captures/<id>.jpg`, insert a `pending` row into SQLite.                                                                                                                                                                            |
| F-10 | Deduplicate on `client_capture_id` — re-uploads after a timeout must not create duplicates.                                                                                                                                                                           |
| F-11 | Caption image via VLM at ingest, no user trigger required. The domain-neutral structure captures a summary, observable details, visible text, spatial relationships, changes from the previous capture, and uncertainties without assuming the scene contains hardware. |
| F-12 | Generate a text embedding of the caption at ingest (Voyage AI API by default; local `sentence-transformers` model behind a `--local-embed` flag) and store it for semantic retrieval. Runs only if captioning succeeded — there's no caption text to embed otherwise. |
| F-13 | Auto-capture ambient context at ingest: hostname, active git repo + branch + commit SHA if the daemon can resolve the frontmost project, wall-clock time.                                                                                                             |
| F-14 | Index caption + tags into SQLite FTS5 (keyword) and the caption embedding into `sqlite-vec` (semantic), in the same SQLite file. Mark row `ready`.                                                                                                                    |
| F-15 | Expose an MCP server over stdio with tools: `search_captures`, `get_capture`, `list_recent`, `get_image`.                                                                                                                                                             |
| F-16 | Serve captions and metadata freely; serve image bytes **only** via explicit `get_image`.                                                                                                                                                                              |




### 4.3 State model

`queued_on_device → uploaded → pending → captioning → ready`

`captioning` covers both the VLM caption call and the embedding call — they run back to back before the row is indexed.

Failure branches: `upload_failed` (device retries), `caption_failed` (row stays `ready` with metadata only, caption and embedding fields null — never blocks retrieval).

### 4.4 Edge cases

- **Caption API unavailable** → store with metadata only; flag for background re-captioning. Capture is never lost to a captioning failure.
- **Captioning fails for a specific image** (e.g. VLM error, malformed response) → row is still retrievable by git context + timestamp alone; caption and embedding stay null rather than blocking the row.
- **Embedding generation fails but captioning succeeded** → row still reaches `ready` with the caption indexed in FTS5; that row is searchable by keyword only until a background job backfills the embedding. Never blocks retrieval or re-triggers captioning.
- **Blurry / dark image** → daemon computes a Laplacian-variance sharpness score; below threshold, the LCD confirmation reads `OK <id> (blurry)` so the user can immediately re-shoot.
- **Daemon restarts mid-caption** → rows in `captioning` are re-queued on boot.
- **Disk growth** → warn at 5 GB; no auto-deletion in v1.
- **Two presses during upload** → second capture queues rather than being dropped.

---



## 5. Out of Scope / Non-Goals

Cut deliberately, with reasons — these are not backlog items, they are decisions:


| Excluded                                                | Why                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spoken/audio annotation (mic, recording, transcription) | Was in the original draft; cut for v1. It added a hardware path (mic), an upload field, a transcription service (`faster-whisper`), and a full failure-mode class (background noise, model latency, audio device drift) without evidence it was needed for retrieval — the edge case "no audio on a capture → caption alone must be sufficient" was already the design's fallback assumption, so it's the load-bearing path, not the backup. Revisit only if the 80% retrieval-hit-rate kill criterion (§2) is missed and caption-prompt iteration doesn't close the gap. |
| Always-on ring buffer / retroactive capture             | The author's evidence showed deliberate capture always succeeded. Solves a problem not in evidence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Serial / UART / power-rail co-timestamping              | Strong long-term moat, wrong first build. Revisit only if v1 sustains usage.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Cross-machine sync, hosted storage, accounts            | v1 is one bench, one laptop.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Mobile app or phone companion                           | The entire point is removing the phone.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Video capture                                           | CoreS3 throughput won't support it usefully and no validated need exists.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Multi-user, permissions, sharing                        | v1 is single-user.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Custom PCB or enclosure                                 | Off-the-shelf M5 modules only.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Object/tool tracking ("where did I leave X")            | Requires continuous observation. Out of the capture model.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Manual text/tag entry on-device                         | No mic and no keyboard on the device — v1's only annotation path is the automatic VLM caption (US-2). A manual tagging UI on a touchscreen is a real feature but a separate one; not needed to test this hypothesis.                                                                                                                                                                                                                                                                                                                                                      |


---



## 6. Technical & Dependency Notes



### Architecture

```
[CoreS3 + Button Unit]
   button → camera
   → WiFi POST /capture (mDNS discovery, SD-backed retry queue)
        ↓
[Laptop daemon :8787]
   → ~/.pcl/captures/*.jpg
   → VLM (caption) → embed(caption) → SQLite (FTS5 + sqlite-vec)
        ↓
[MCP server, stdio]
   search_captures · get_capture · list_recent · get_image
        ↓
[Cursor · Claude Code · any MCP client]
```



### Stack

- **Firmware:** M5Unified + ESP32 Arduino core, or ESP-IDF. Button Unit on Port B (GPIO), single-gesture (press only).
- **Daemon:** Python. FastAPI + SQLite (FTS5 + `sqlite-vec`) + `zeroconf`. MCP via the Python SDK, stdio transport.
- **Captioning:** Anthropic Messages API, image + short structured prompt.
- **Embeddings:** Voyage AI API (`voyage-3-lite` or equivalent short-text model) on the caption text at ingest; local fallback via a small `sentence-transformers` model behind `-local-embed`, mirroring `-local-caption`.
- **Vector index:** `sqlite-vec` — lives in the same SQLite file as FTS5, no separate database or service to run.



### Data model

```sql
captures(
  id TEXT PRIMARY KEY,
  client_capture_id TEXT UNIQUE,
  created_at TEXT,          -- ISO-8601, daemon clock
  device_ts INTEGER,
  image_path TEXT,
  caption TEXT,
  tags TEXT,                -- JSON array
  git_repo TEXT, git_branch TEXT, git_sha TEXT,
  sharpness REAL,
  state TEXT
);
captures_fts USING fts5(caption, tags, content='captures');

-- sqlite-vec virtual table, one row per capture with a non-null caption
captures_vec USING vec0(
  capture_id TEXT PRIMARY KEY,
  embedding FLOAT[512]      -- dimension set by the embedding model in use
);
```

`search_captures` queries both `captures_fts` and `captures_vec` and merges/reranks the results into one ranked list (see US-3).

### Privacy / egress

Images are stored **locally only**. Egress is two API calls per image at ingest — one to the VLM for captioning, one to the embedding provider for the caption's vector — neither sends image bytes a second time, and neither is retained by the daemon beyond what's stored locally. This is the honest limitation and it matters for anyone in an NDA lab. `--local-caption` and `--local-embed` flags (local VLM via Ollama, local sentence-transformers model) should both be scaffolded in v1 even if quality is worse, because they're far harder to retrofit later — and together they're now the only way to run the full capture-to-retrieval loop fully offline, since there's no transcript path to fall back on.

### Key risks


| #      | Risk                                                                                                                                                                                                  | Severity | Mitigation                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **R1** | **CoreS3 camera is a 0.3MP GC0308 (640×480).** It will not resolve silkscreen, resistor bands, or bent pins — the strongest reason to prefer a device over a phone.                                   | **High** | Accept for v1: the hypothesis under test is *workflow*, not optics. Log every capture where resolution was insufficient. If that count is high while usage is also high, v1.5 is a camera swap (Unit CamS3 / OV3660 or better with a macro lens), not a rebuild.                                                                                                                                                                 |
| R2     | Novelty decay — usage collapses after initial setup                                                                                                                                                   | High     | Track ongoing capture volume during regular bench work before investing in v1.5 hardware.                                                                                                                                                                                                                                                                                                                                        |
| R3     | Caption quality too generic to retrieve against — and with voice removed, the caption is now the *only* human-legible text signal per capture (no spoken note to compensate for a wrong or vague one) | High     | Domain-neutral structured caption prompt (F-11); hybrid retrieval (F-14: FTS5 keyword + `sqlite-vec` semantic, F-12) directly targets this — semantic search catches paraphrase/synonym queries a keyword-only index would miss. The ≥80% retrieval-hit-rate metric (§2) is still the pass/fail bar. If hybrid retrieval still isn't clearing it, iterate the caption prompt before reconsidering scope — don't re-add voice as a patch. |
| R4     | ESP32 upload latency breaks the <2 s confirmation                                                                                                                                                     | Medium   | Confirm on capture, not on upload completion; upload asynchronously.                                                                                                                                                                                                                                                                                                                                                             |
| R5     | Fixed-mount vs handheld ergonomics unresolved                                                                                                                                                         | Medium   | Try both during v1 validation. Which one wins determines whether a repeatable vantage point (and therefore day-to-day diffing) is a real v2 feature.                                                                                                                                                                                                                                                                             |
| R6     | Battery / power management                                                                                                                                                                            | Low      | Run tethered on USB-C at the bench.                                                                                                                                                                                                                                                                                                                                                                                              |


---
