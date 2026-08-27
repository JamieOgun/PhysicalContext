# CoreS3 Capture Firmware

Firmware for the M5Stack CoreS3-Lite used by the Physical Context Layer. It
shows a live camera viewfinder, captures a JPEG when the external trigger is
pressed, uploads it to the local daemon, and displays captioning progress.

## Hardware

- M5Stack CoreS3-Lite
- M5Stack ATOM Lite trigger
- Grove/HY2.0 cable

The CoreS3 reads an active-low trigger from either Port A signal pin:

| CoreS3 wire | GPIO | ATOM Lite GPIO |
| --- | ---: | ---: |
| White | 1 | 32 |
| Yellow | 2 | 26 |
| Red | 5V | 5V |
| Black | GND | GND |

## User Interface

The default screen is a live camera viewfinder with a framing reticle, Wi-Fi
status, and capture count. After a capture, the photographed frame freezes
briefly and the lower status bar moves through these states:

```text
Capturing -> Queued -> Sending -> Captioning -> Saved
```

Network retries, delayed processing, missing captions, blurry images, and
capture errors are also shown. Upload and caption-status work runs in
background tasks, so the live preview can resume while processing continues.

## Configuration

Install [PlatformIO](https://platformio.org/) and create the two local header
files below. They are ignored by Git.

Copy the daemon configuration template:

```sh
cp include/local_config.example.h include/local_config.h
```

Set `DAEMON_URL` in `include/local_config.h` to the LAN address of the computer
running the daemon. Keep the `/capture` path:

```cpp
#pragma once

constexpr char DAEMON_URL[] = "http://192.168.1.100:8787/capture";
```

Create `include/secrets.h` with the Wi-Fi credentials:

```cpp
#pragma once

constexpr char WIFI_SSID[] = "your-network";
constexpr char WIFI_PASSWORD[] = "your-password";
```

## Build And Flash

Start the daemon from the repository root. Binding to `0.0.0.0` allows the
CoreS3 to reach it over the local network.

```sh
cd daemon
uv run uvicorn physical_context.app:app --host 0.0.0.0 --port 8787
```

In another terminal, build and upload the firmware:

```sh
cd firmware/cores3_smoke_test
pio run
pio run -t upload
```

Open the serial monitor for connection and upload diagnostics:

```sh
pio device monitor
```

## Main Files

- `src/main.cpp`: camera, trigger, upload queue, and caption-status polling
- `src/status_ui.cpp`: viewfinder overlays, status states, and animation
- `include/status_ui.h`: status UI interface and state definitions
- `platformio.ini`: CoreS3 board and library configuration

The preview frame rate and captured-frame freeze duration are controlled by
`PREVIEW_FRAME_MS` and `CAPTURE_FREEZE_MS` in `src/main.cpp`.
