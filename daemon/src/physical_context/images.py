from pathlib import Path

import cv2

from physical_context.image_quality import ImageDecodeError

MAX_IMAGE_EDGE = 1024


def load_downscaled_jpeg(image_path: Path, *, max_edge: int = MAX_IMAGE_EDGE) -> bytes:
    """Decode a capture and re-encode it with its longest edge capped.

    Images are only ever handed out one at a time, so the cap is about the cost
    of a single look rather than a bandwidth budget: a 1024px edge is roughly a
    thousand tokens of the caller's context.
    """
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ImageDecodeError(f"Could not decode image: {image_path.name}")

    height, width = image.shape[:2]
    longest_edge = max(height, width)
    if longest_edge > max_edge:
        scale = max_edge / longest_edge
        image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    encoded, buffer = cv2.imencode(".jpg", image)
    if not encoded:
        raise ImageDecodeError(f"Could not encode image: {image_path.name}")
    return bytes(buffer)
