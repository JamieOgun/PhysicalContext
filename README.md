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
