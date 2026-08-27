# Physical Context Layer

## Hardware

- Core device: M5Stack CoreS3-Lite ESP32-S3 IoT.
- Trigger device for current prototype: M5Stack ATOM Lite ESP32-PICO.
- CoreS3-Lite exposes one Grove/HY2.0-4P port: Port A.
- CoreS3-Lite Port A pinout: black = GND, red = 5V, yellow = GPIO2, white = GPIO1.
- ATOM Lite Grove pinout: black = GND, red = 5V, yellow = GPIO26, white = GPIO32.
- Working trigger path: ATOM Lite button drives Grove output low; CoreS3-Lite reads GPIO1/GPIO2 with pullups and increments on LOW.

## Firmware Projects

- `firmware/cores3_smoke_test`: CoreS3-Lite display and trigger receiver.
- `firmware/atom_lite_trigger`: ATOM Lite button-to-Grove trigger sender.

## Daemon Development

Requires Python 3.11+ and `uv`.

```sh
cd daemon
cp .env.example .env
uv sync
uv run pcl-daemon
```

The daemon listens on port `8787` and initializes `~/.pcl/captures/` plus
`~/.pcl/physical_context.db`. Configure it with the `PCL_*` values in `.env`.

```sh
# Auto-reloading development server
uv run uvicorn physical_context.app:app --reload --host 0.0.0.0 --port 8787

# Checks
uv run ruff check .
uv run pytest
```

## MCP Server

The capture store is exposed to MCP clients (Claude Code, Cursor) over stdio by
`pcl-mcp`. It reads the same `~/.pcl` database as the daemon and needs no
running daemon of its own, though captures only appear once the daemon has
ingested and captioned them.

```sh
cd daemon
uv run pcl-mcp
```

To register it with Claude Code:

```sh
claude mcp add physical-context -- uv run --directory /absolute/path/to/daemon pcl-mcp
```

Tools, cheapest first — prefer the earliest one that answers the question:

| Tool | Returns | Use for |
| --- | --- | --- |
| `search_captures` | Capture IDs, timestamps, caption summary lines, tags | Finding captures by description; hybrid keyword + semantic |
| `list_recent` | The newest captures, any state | "What was I just working on" |
| `get_capture` | The full structured caption and metadata for one ID | Reading the whole caption |
| `get_image` | One photo, downscaled to a 1024px longest edge | When the pixels matter and the caption does not answer |

Image bytes are served **only** by `get_image`, one capture at a time. Without
`PCL_VOYAGE_API_KEY` set, search degrades to keyword-only and says so in the
response note.
