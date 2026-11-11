#!/usr/bin/env python3
"""
Draw a generic device frame around a screen of any size and register it in devices.json.

The frames are original drawings, free to redistribute. They are the defaults so a fresh
clone renders without vendor artwork. Swap in official bezels later with prepare_device.py.

  make_frame.py --style ios --screen 1206x2622 --device iphone-67
  make_frame.py --style android --screen 1080x2400 --device android-phone --captures android
  make_frame.py --style tablet --screen 2064x2752 --device ipad-13 --status-bar ipados
  make_frame.py --style landscape --screen 2853x2007 --device iphone-duo --captures duo

Styles: ios (Dynamic Island), android (punch-hole camera), tablet, landscape (thin bezel,
no camera). The screen size must match the captures the device reads, or render.py stops.
Writes devices/generic-<style>-<W>x<H>.png plus its mask and silhouette. With --device it
also writes the devices.json entry, keeping the layout block of an existing entry.
"""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from prepare_device import measure, silhouette

SKILL_DIR = Path(__file__).resolve().parent
SS = 2

LAYOUTS = {
    "ios": {"headlineTop": 0.064, "sidePad": 0.05, "verbMax": 250, "verbMin": 130, "descMax": 110,
            "descMin": 60, "descLines": 2, "headlineGap": 0.01, "deviceTop": 0.31, "deviceWidth": 0.86,
            "minHeadlineDeviceGap": 0.018, "perspective": 3400},
    "android": {"headlineTop": 0.052, "sidePad": 0.06, "verbMax": 210, "verbMin": 110, "descMax": 92,
                "descMin": 50, "descLines": 2, "headlineGap": 0.01, "deviceTop": 0.335, "deviceWidth": 0.7,
                "minHeadlineDeviceGap": 0.02, "perspective": 2600},
    "tablet": {"headlineTop": 0.05, "sidePad": 0.06, "verbMax": 230, "verbMin": 110, "descMax": 100,
               "descMin": 56, "descLines": 2, "headlineGap": 0.01, "deviceTop": 0.27, "deviceWidth": 0.84,
               "minHeadlineDeviceGap": 0.018, "perspective": 4800},
    "landscape": {"headlineTop": 0.045, "sidePad": 0.06, "verbMax": 200, "verbMin": 100, "descMax": 88,
                  "descMin": 50, "descLines": 1, "headlineGap": 0.008, "deviceTop": 0.235, "deviceWidth": 0.8,
                  "minHeadlineDeviceGap": 0.018, "perspective": 6000},
}

GEOMETRY = {
    # bezel, metal band and screen corner radius as fractions of the screen's short side
    "ios": {"bezel": 0.030, "metal": 0.013, "radius": 0.137, "buttons": True},
    "android": {"bezel": 0.028, "metal": 0.010, "radius": 0.090, "buttons": True},
    "tablet": {"bezel": 0.036, "metal": 0.008, "radius": 0.030, "buttons": False},
    "landscape": {"bezel": 0.026, "metal": 0.008, "radius": 0.035, "buttons": False},
}


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def shade(rgb, f):
    return tuple(max(0, min(255, round(c * f))) for c in rgb)


def draw_frame(style, sw, sh, color):
    g = GEOMETRY[style]
    short = min(sw, sh)
    bezel, metal, radius = (round(g[k] * short) for k in ("bezel", "metal", "radius"))
    button = round(0.008 * short) if g["buttons"] else 0
    pad = bezel + metal
    fw, fh = sw + 2 * pad + 2 * button, sh + 2 * pad
    ox = button
    body = hex_rgb(color)

    img = Image.new("RGBA", (fw * SS, fh * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    S = lambda *v: [round(x * SS) for x in v]

    if button:
        rail = (*shade(body, 0.85), 255)
        unit = sh / 100
        for side, spans in (("left", ((17, 21), (26, 33), (35, 42))), ("right", ((27, 37),))):
            x0 = 0 if side == "left" else fw - button - round(button * 0.2)
            for a, b in spans:
                d.rounded_rectangle(S(x0, a * unit, x0 + button * 1.6, b * unit), radius=button * SS * 0.6, fill=rail)

    outer_r = radius + pad
    d.rounded_rectangle(S(ox, 0, ox + sw + 2 * pad, fh), radius=outer_r * SS, fill=(*shade(body, 0.75), 255))
    edge = max(2, round(metal * 0.35))
    d.rounded_rectangle(S(ox + edge, edge, ox + sw + 2 * pad - edge, fh - edge), radius=(outer_r - edge) * SS,
                        fill=(*shade(body, 1.18), 255))
    d.rounded_rectangle(S(ox + metal * 0.6, metal * 0.6, ox + sw + 2 * pad - metal * 0.6, fh - metal * 0.6),
                        radius=(outer_r - metal * 0.6) * SS, fill=(*body, 255))
    d.rounded_rectangle(S(ox + metal, metal, ox + sw + 2 * pad - metal, fh - metal), radius=(radius + bezel) * SS,
                        fill=(8, 8, 10, 255))
    sx, sy = ox + pad, pad
    d.rounded_rectangle(S(sx, sy, sx + sw, sy + sh), radius=radius * SS, fill=(0, 0, 0, 0))

    camera_y = None
    if style == "ios":
        iw, ih, top = 0.311 * sw, 0.092 * sw, 0.027 * sw
        d.rounded_rectangle(S(sx + (sw - iw) / 2, sy + top, sx + (sw + iw) / 2, sy + top + ih), radius=ih / 2 * SS,
                            fill=(0, 0, 0, 255))
    elif style == "android":
        dia, cy = 0.036 * sw, 0.050 * sw
        camera_y = sy + cy
        d.ellipse(S(sx + sw / 2 - dia / 2, camera_y - dia / 2, sx + sw / 2 + dia / 2, camera_y + dia / 2), fill=(0, 0, 0, 255))

    return img.resize((fw, fh), Image.LANCZOS), camera_y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", choices=sorted(GEOMETRY), required=True)
    ap.add_argument("--screen", required=True, help="WxH in capture pixels, e.g. 1206x2622")
    ap.add_argument("--device", help="devices.json id to create or update")
    ap.add_argument("--canvas", help="store canvas WxH; default keeps the entry's or uses the screen size")
    ap.add_argument("--color", default="#4a4d52", help="body colour")
    ap.add_argument("--label")
    ap.add_argument("--store", help="store slot this device's tiles go to, documentation only")
    ap.add_argument("--captures", help="capture set the device reads (phone, tablet, duo, android ...)")
    ap.add_argument("--status-bar", choices=["ios", "ipados", "android", "none"])
    args = ap.parse_args()

    sw, sh = (int(v) for v in args.screen.lower().split("x"))
    frame, camera_y = draw_frame(args.style, sw, sh, args.color)
    name = f"generic-{args.style}-{sw}x{sh}"
    path = SKILL_DIR / "devices" / f"{name}.png"
    frame.save(path)
    _, screen, mask, hole = measure(path, 10, 3)
    mask.save(path.with_suffix(".mask.png"))
    silhouette(frame, hole).save(path.with_suffix(".silhouette.png"))

    entry = {"frame": path.name, "frameSize": list(frame.size), "screen": list(screen), "bleed": 3,
             "mask": f"{name}.mask.png", "silhouette": f"{name}.silhouette.png",
             "source": f"Generic {args.style} frame drawn by make_frame.py"}
    if camera_y is not None:
        entry["cameraCenterY"] = round(camera_y, 1)
    print(json.dumps(entry))
    if not args.device:
        return

    devices_path = SKILL_DIR / "devices" / "devices.json"
    devices = json.loads(devices_path.read_text())
    old = devices.get(args.device, {})
    for key in ("cameraCenterY", "statusBarCenterY", "statusBarUnit", "rim"):
        old.pop(key, None)
    canvas = [int(v) for v in args.canvas.lower().split("x")] if args.canvas else old.get("canvas", [sw, sh])
    default_bar = {"ios": "ios", "android": "android", "tablet": "ipados", "landscape": None}[args.style]
    bar = args.status_bar or old.get("statusBar", default_bar)
    merged = {"label": args.label or old.get("label", args.device), "store": args.store or old.get("store", ""),
              "canvas": canvas, **entry, "statusBar": None if bar == "none" else bar,
              "layout": old.get("layout", LAYOUTS[args.style])}
    captures = args.captures or old.get("captures")
    if captures and captures != "phone":
        merged["captures"] = captures
    devices[args.device] = merged
    devices_path.write_text(json.dumps(devices, indent=2) + "\n")
    print(f"devices.json: {args.device} -> {path.name}, canvas {canvas[0]}x{canvas[1]}")


if __name__ == "__main__":
    main()
