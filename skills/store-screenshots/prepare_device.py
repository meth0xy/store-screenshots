#!/usr/bin/env python3
"""
Measure the screen opening of a device frame PNG and write its screen mask.

The frame must have a transparent screen. The opening is flood-filled from the
frame centre, so the mask follows the real corner radius and any cut-outs.

  prepare_device.py devices/iphone-17-pro-silver.png
  prepare_device.py devices/pixel-6-pro.png --dilate 3

Prints the `screen` rectangle and `bleed` for devices.json and writes
<frame>.mask.png, cropped to the screen rectangle grown by `bleed` on every side,
plus <frame>.silhouette.png, a solid low-resolution copy of the device body that
the renderer stacks to draw the side wall of a tilted device.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

HOLE = 128


def measure(frame_path, alpha_threshold, dilate):
    frame = Image.open(frame_path).convert("RGBA")
    w, h = frame.size
    solid = frame.getchannel("A").point(lambda a: 0 if a < alpha_threshold else 255)
    if solid.getpixel((w // 2, h // 2)) != 0:
        raise SystemExit(f"{frame_path}: the frame centre is not transparent, no screen opening found")

    ImageDraw.floodfill(solid, (w // 2, h // 2), HOLE)
    hole = solid.point(lambda v: 255 if v == HOLE else 0)
    left, top, right, bottom = hole.getbbox()

    # The frame's anti-aliased inner edge is half transparent; growing the mask a few
    # pixels puts screen pixels under it instead of the canvas background.
    if dilate:
        hole = hole.filter(ImageFilter.MaxFilter(dilate * 2 + 1))
    grown = (left - dilate, top - dilate, right + dilate, bottom + dilate)
    alpha = hole.crop(grown)
    white = Image.new("L", alpha.size, 255)
    # CSS mask-image reads the alpha channel, so the opening goes into alpha.
    mask = Image.merge("RGBA", (white, white, white, alpha))
    return frame, (left, top, right - left, bottom - top), mask, hole


def silhouette(frame, hole, scale=2):
    """Device body with the screen filled in, tinted with the colour of the frame's outer band."""
    body = ImageChops.lighter(frame.getchannel("A"), hole)
    ring = np.asarray(ImageChops.subtract(body, body.filter(ImageFilter.MinFilter(9)))).reshape(-1)
    band = np.asarray(frame.convert("RGB")).reshape(-1, 3)[ring > 200]
    tint = tuple(int(c) for c in np.median(band, axis=0)) if len(band) else (120, 120, 120)
    small = (frame.width // scale, frame.height // scale)
    out = Image.new("RGBA", small, tint + (0,))
    out.putalpha(body.resize(small, Image.LANCZOS))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frame")
    ap.add_argument("--alpha-threshold", type=int, default=10)
    ap.add_argument("--dilate", type=int, default=3)
    args = ap.parse_args()

    frame_path = Path(args.frame)
    frame, screen, mask, hole = measure(frame_path, args.alpha_threshold, args.dilate)
    mask_path = frame_path.with_suffix(".mask.png")
    mask.save(mask_path)
    side_path = frame_path.with_suffix(".silhouette.png")
    silhouette(frame, hole).save(side_path)
    print(json.dumps({"frameSize": list(frame.size), "screen": list(screen), "bleed": args.dilate,
                      "mask": mask_path.name, "silhouette": side_path.name}))


if __name__ == "__main__":
    main()
