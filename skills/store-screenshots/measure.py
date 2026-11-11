#!/usr/bin/env python3
"""
Find the rectangle of a UI element in a simulator capture, for set.json rects.

  measure.py CAPTURE --at 700,790              same-coloured region around a point (button, chip, pill)
  measure.py CAPTURE --ink 40,415,700,75       bounds of everything that differs from the box's edge colour (text)
  measure.py CAPTURE --grid 600,690,460,200    zoomed crop with a coordinate grid, written to the temp dir

Coordinates are capture pixels. --at and --ink print {"rect": [x, y, w, h], "color": "#rrggbb"}.
"""

import argparse
import tempfile
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def numbers(text, count):
    values = [int(v) for v in text.split(",")]
    if len(values) != count:
        raise SystemExit(f"expected {count} comma-separated numbers, got {text}")
    return values


def hex_color(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(int(c) for c in rgb))


def region_at(image, x, y, tol, window=7):
    # A point picked by eye often lands on a glyph or inside a letter's counter,
    # which has the button colour but is its own small region. The seed colour is
    # the neighbourhood median and the largest region touching the window wins.
    arr = np.asarray(image).astype(np.int16)
    x0, y0 = max(x - window, 0), max(y - window, 0)
    seed = np.median(arr[y0:y + window + 1, x0:x + window + 1].reshape(-1, 3), axis=0)
    # fromarray shares numpy's read-only buffer and floodfill writes into it are lost, hence the copy.
    mask = Image.fromarray(((np.abs(arr - seed).sum(axis=2) <= tol) * 255).astype(np.uint8)).copy()
    label = 0
    for sy, sx in zip(*np.nonzero(np.asarray(mask)[y0:y + window + 1, x0:x + window + 1] == 255)):
        start = (int(sx) + x0, int(sy) + y0)
        if mask.getpixel(start) == 255 and label < 254:
            label += 1
            ImageDraw.floodfill(mask, start, label)
    if not label:
        raise SystemExit("no pixel near the point has the neighbourhood colour")
    labels = np.asarray(mask)
    best = max(range(1, label + 1), key=lambda v: np.count_nonzero(labels == v))
    ys, xs = np.nonzero(labels == best)
    return [int(xs.min()), int(ys.min()), int(xs.max() - xs.min()) + 1, int(ys.max() - ys.min()) + 1], hex_color(seed)


def ink_in(image, box, tol):
    x, y, w, h = box
    arr = np.asarray(image.crop((x, y, x + w, y + h))).astype(np.int16)
    edge = np.concatenate([arr[0], arr[-1], arr[:, 0], arr[:, -1]])
    bg = np.median(edge, axis=0)
    ys, xs = np.nonzero(np.abs(arr - bg).sum(axis=2) > tol)
    if not len(xs):
        raise SystemExit("nothing in the box differs from its edge colour")
    return [x + int(xs.min()), y + int(ys.min()), int(xs.max() - xs.min()) + 1, int(ys.max() - ys.min()) + 1], hex_color(bg)


def grid(image, box, zoom, out):
    x, y, w, h = box
    crop = image.crop((x, y, x + w, y + h)).resize((w * zoom, h * zoom), Image.NEAREST)
    draw = ImageDraw.Draw(crop)
    font = ImageFont.load_default()
    for gx in range(x - x % 10 + 10, x + w, 10):
        major = gx % 50 == 0
        draw.line([((gx - x) * zoom, 0), ((gx - x) * zoom, h * zoom)], fill=(255, 0, 255) if major else (0, 200, 255), width=1)
        if major:
            draw.text(((gx - x) * zoom + 2, 2), str(gx), fill=(255, 0, 255), font=font)
    for gy in range(y - y % 10 + 10, y + h, 10):
        major = gy % 50 == 0
        draw.line([(0, (gy - y) * zoom), (w * zoom, (gy - y) * zoom)], fill=(255, 0, 255) if major else (0, 200, 255), width=1)
        if major:
            draw.text((2, (gy - y) * zoom + 2), str(gy), fill=(255, 0, 255), font=font)
    crop.save(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--at", help="x,y inside the element")
    mode.add_argument("--ink", help="x,y,w,h around the text")
    mode.add_argument("--grid", help="x,y,w,h to zoom into")
    ap.add_argument("--tol", type=int, help="summed RGB difference, default 12 for --at (chips sit about 25 from their card) and 60 for --ink (soft gradients stay below)")
    ap.add_argument("--zoom", type=int, default=3)
    args = ap.parse_args()

    image = Image.open(args.capture).convert("RGB")
    if args.grid:
        out = Path(tempfile.gettempdir()) / f"{Path(args.capture).stem}-grid.png"
        print(grid(image, numbers(args.grid, 4), args.zoom, out))
        return
    if args.at:
        rect, color = region_at(image, *numbers(args.at, 2), 12 if args.tol is None else args.tol)
    else:
        rect, color = ink_in(image, numbers(args.ink, 4), 60 if args.tol is None else args.tol)
    print(json.dumps({"rect": rect, "color": color}))


if __name__ == "__main__":
    main()
