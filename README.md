# Physical Context Layer

Physical Context Layer gives AI coding agents access to relevant context from
the physical world. It is a dedicated, one-button camera for a workbench: take
a picture of a circuit, mechanism, tool, sketch, or failure state and make it
searchable from an agent such as Claude Code or Cursor within seconds.

The product is intended for robotics and hardware work where important context
exists outside the computer. It removes the usual phone-to-laptop workflow of
taking a photo, transferring it, and explaining it again in a chat.

## How It Works

1. The CoreS3 displays a live camera view so the user can frame the scene.
2. A single button press captures and queues the image.
3. The device uploads the image to the Physical Context daemon over local Wi-Fi.
4. The daemon stores the image, records time and project context, and generates
   a structured visual caption.
5. The caption is indexed for keyword and semantic search.
6. A connected coding agent can search captures by description and request the
   original image only when the pixels are needed.

The device shows each stage directly on its display: capturing, sending,
captioning, saved, retrying, or failed. Upload and caption processing run in the
background, so the camera can return to the live viewfinder for another capture.

## Product Goals

- Capture real-world engineering context without using a phone.
- Keep the interaction to one physical button press.
- Make previous observations retrievable by natural-language description.
- Give agents text context by default and image data only on explicit request.
- Preserve useful captures even when captioning or embedding services fail.

## System Overview

```text
M5Stack CoreS3 + trigger button
            |
            | JPEG over local Wi-Fi
            v
Physical Context daemon
  - local image storage
  - structured visual captioning
  - keyword and semantic indexing
            |
            | MCP tools
            v
Claude Code, Cursor, or another MCP client
```

Captured images and metadata are stored locally under `~/.pcl`. When remote
captioning or embedding providers are configured, image or caption data is sent
to those providers for processing. See [the product requirements](docs/prd.md)
for the full scope and design decisions, and the
[CoreS3 firmware guide](firmware/cores3_smoke_test/README.md) for device setup.

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
