#!/usr/bin/env python3
"""
Render store screenshots for every locale x device x slide of a canvas project.

  render.py PROJECT                       all locales, devices and slides
  render.py PROJECT --locale en-US        one locale
  render.py PROJECT --device iphone-67 --slide 01-plan
  render.py PROJECT --compare             also build baseline-vs-new sheets
  render.py PROJECT --emit-config --locale de-DE --device iphone-67 --url-prefix /files --state -
                                          print the renderer configs instead of rendering,
                                          optionally with set.json and locales from stdin

PROJECT contains set.json, locales/<locale>.json and sim/<locale>/<slide>.png.
Devices with "captures": "tablet" read the folder named in set.json capture.sets.tablet.dir.
A locale file with "captures": "<other locale>" reads that locale's captures.
Output: PROJECT/out/<locale>/<device>/<slide>.jpg (only store files, the upload
scripts take every image in the folder), contact sheets in PROJECT/out/_sheets/
and with --compare PROJECT/out/compare/<locale>-<device>.jpg.
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

SKILL_DIR = Path(__file__).resolve().parent
ORIGIN = "http://canvas.local"
URL_PREFIX = f"{ORIGIN}/f"
DEFAULT_STYLE = {"gloss": 0.06, "deviceShadow": 1.0, "thicknessRatio": 0.09}
DEFAULT_STATUS_BAR = {"time": "9:41", "color": "#000000"}
DEFAULT_BAND = {"ios": [66, 128]}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def merged(*dicts):
    out = {}
    for d in dicts:
        for key, value in (d or {}).items():
            out[key] = merged(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def resolve(value, keys, device_keys=("default",)):
    """Pick the variant wherever set.json uses {"byLocale": {...}} or {"byDevice": {...}}, trying keys in order."""
    if isinstance(value, dict):
        for kind, order in (("byLocale", keys), ("byDevice", device_keys)):
            if set(value) == {kind}:
                table = value[kind]
                for key in order:
                    if key in table:
                        return resolve(table[key], keys, device_keys)
                raise SystemExit(f"{kind} has none of {', '.join(order)}: {table}")
        return {k: resolve(v, keys, device_keys) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, keys, device_keys) for v in value]
    return value


def url_for(path):
    return f"{URL_PREFIX}{quote(str(Path(path).resolve()))}"


def hex_color(rgb):
    return "#{:02x}{:02x}{:02x}".format(*(int(c) for c in rgb))


def clean_capture(src, dst, band):
    """Blend the status bar rows away column by column so the renderer can draw a clean one."""
    arr = np.asarray(Image.open(src).convert("RGB")).astype(np.float32)
    y0, y1 = band
    above, below = arr[y0 - 1], arr[y1 + 1]
    t = np.linspace(0.0, 1.0, y1 - y0 + 3)[1:-1, None, None]
    arr[y0:y1 + 1] = above[None] * (1 - t) + below[None] * t
    dst.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.round().astype(np.uint8)).save(dst)
    return arr


def ring_color(arr, rect, gap=6, width=8):
    x, y, w, h = rect
    H, W = arr.shape[:2]
    x0, y0 = max(x - gap - width, 0), max(y - gap - width, 0)
    x1, y1 = min(x + w + gap + width, W), min(y + h + gap + width, H)
    keep = np.ones((y1 - y0, x1 - x0), bool)
    keep[max(y - gap - y0, 0):min(y + h + gap - y0, y1 - y0), max(x - gap - x0, 0):min(x + w + gap - x0, x1 - x0)] = False
    return hex_color(np.median(arr[y0:y1, x0:x1][keep], axis=0))


def edge_color(arr):
    ring = np.concatenate([arr[:4].reshape(-1, 3), arr[-4:].reshape(-1, 3), arr[:, :4].reshape(-1, 3), arr[:, -4:].reshape(-1, 3)])
    return hex_color(np.median(ring, axis=0))


class Project:
    def __init__(self, root, state=None):
        """state: {"set": ..., "locales": {code: ...}} replaces the files, for unsaved editor changes."""
        state = state or {}
        self.root = Path(root).resolve()
        self.set = state.get("set") or load_json(self.root / "set.json")
        self.devices = load_json(SKILL_DIR / "devices" / "devices.json")
        given = state.get("locales") or {}
        self.locales = {code: given.get(code) or load_json(self.root / "locales" / f"{code}.json")
                        for code in self.set["locales"]}
        self._captures = {}

    def path(self, rel):
        return (self.root / rel).resolve()

    def capture_locale(self, locale):
        return self.locales[locale].get("captures", locale)

    def lookup(self, locale):
        """byLocale keys for a locale: its own, then the locale whose captures it reads, then default."""
        return list(dict.fromkeys([locale, self.capture_locale(locale), "default"]))

    def capture_set(self, device_id):
        """Capture folder and status bar band for a device: phones read sim/, other sets their own folder."""
        base = self.set.get("capture", {})
        name = base.get("devices", {}).get(device_id) or self.devices[device_id].get("captures", "phone")
        platform = base.get("platform", "ios")
        conf = merged({"dir": "sim", "statusBarBand": base.get("statusBarBand", DEFAULT_BAND.get(platform))},
                      base.get("sets", {}).get(name))
        if name != "phone" and name not in base.get("sets", {}):
            raise SystemExit(f"device {device_id} reads '{name}' captures; set.json capture.sets has no '{name}'")
        return name, conf

    def device_keys(self, device_id):
        """byDevice keys for a device: its id, its capture set (phone, tablet), then default."""
        return list(dict.fromkeys([device_id, self.capture_set(device_id)[0], "default"]))

    def resolved_slides(self, locale, device_id):
        return [resolve(s, self.lookup(locale), self.device_keys(device_id)) for s in self.set["slides"]]

    def capture(self, locale, slide, device_id):
        name = slide.get("sim") or f"{slide['id']}.png"
        source = self.capture_locale(locale)
        _, conf = self.capture_set(device_id)
        src = self.root / conf["dir"] / source / name
        if not src.exists():
            raise SystemExit(f"missing capture {src}")
        band = conf.get("statusBarBand")
        key = (conf["dir"], source, name, tuple(band or ()))
        if key not in self._captures:
            if not band:
                self._captures[key] = (src, np.asarray(Image.open(src).convert("RGB")).astype(np.float32))
            else:
                dst = self.root / ".cache" / conf["dir"] / f"band-{band[0]}-{band[1]}" / source / name
                if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
                    arr = np.asarray(Image.open(dst).convert("RGB")).astype(np.float32)
                else:
                    arr = clean_capture(src, dst, band)
                self._captures[key] = (dst, arr)
        return self._captures[key]

    def slide_config(self, locale, device_id, slide, sizes, mode):
        device = self.devices[device_id]
        loc = self.locales[locale]
        text = loc["slides"].get(slide["id"])
        if not text:
            raise SystemExit(f"locales/{locale}.json has no text for slide {slide['id']}")
        sim_path, arr = self.capture(locale, slide, device_id)
        sim_h, sim_w = arr.shape[:2]
        screen_w, screen_h = device["screen"][2:]
        if abs(sim_h / sim_w - screen_h / screen_w) > 0.012:
            raise SystemExit(f"{sim_path}: capture is {sim_w}x{sim_h}, the {device_id} screen opening is {screen_w}x{screen_h}. "
                             f"Aspect ratios differ; make a matching frame with make_frame.py --screen {sim_w}x{sim_h}")

        fonts = merged(self.set.get("fonts"), loc.get("fonts"))
        if not fonts:
            fonts = {"display": str(SKILL_DIR / "fonts" / "ArchivoBlack-Regular.ttf")}
        headline = merged({"font": "display", "verbColor": "#FFFFFF", "descColor": "rgba(255,255,255,0.88)",
                           "shadow": "0 8px 28px rgba(0,0,0,0.28)", "verbTracking": "-0.01em",
                           "descTracking": "0.01em", "descWordSpacing": "0.08em"},
                          self.set.get("headline"), loc.get("headline"))
        layout = merged(device["layout"], self.set.get("layout", {}).get(device_id), slide.get("layout"))
        style = merged(DEFAULT_STYLE, self.set.get("style"))
        style["thickness"] = style["thicknessRatio"] * layout["deviceWidth"] * device["canvas"][0]

        breakouts, vacate = [], []
        for bo in slide.get("breakouts", []):
            bo = dict(bo)
            bo["rect"] = bo.pop("rect", None) or bo.pop("anchor")
            if "at" in bo:
                bo["dx"], bo["dy"] = bo["at"][0] - bo["rect"][0], bo["at"][1] - bo["rect"][1]
            if bo["type"] == "callout":
                bo["text"] = resolve(text.get("callouts", {}).get(bo["text"], bo["text"]),
                                     self.lookup(locale), self.device_keys(device_id))
                bo["style"] = merged({"font": "display", "size": 40, "bg": "#FFFFFF", "fg": "#000000", "padX": 28, "padY": 14},
                                     bo.get("style"))
            if bo.get("vacate"):
                # The element's anti-aliased edge sits just outside its rect and would stay as an outline.
                pad = bo.get("vacatePad", 4)
                x, y, w, h = bo["rect"]
                vacate.append({"rect": [x - pad, y - pad, w + 2 * pad, h + 2 * pad], "color": ring_color(arr, bo["rect"]),
                               "radius": bo.get("radius", h / 2) + pad})
            breakouts.append(bo)

        props = [merged(p, {"url": url_for(self.path(p["src"]))}) for p in slide.get("props", [])]
        background = dict(resolve(self.set.get("background", {}), self.lookup(locale), self.device_keys(device_id)))
        if background.get("image"):
            background["url"] = url_for(self.path(background.pop("image")))
        elif not background.get("css"):
            background["css"] = "radial-gradient(120% 80% at 80% 60%, #5b8def 0%, #1b2440 60%)"

        return {
            "mode": mode,
            "sizes": sizes,
            "dir": loc.get("dir", "ltr"),
            "lang": locale,
            "canvas": {"w": device["canvas"][0], "h": device["canvas"][1]},
            "fonts": {family: url_for(self.path(p)) for family, p in fonts.items()},
            "headline": headline,
            "layout": layout,
            "style": style,
            "background": background,
            "text": {"verb": text["verb"], "desc": text["desc"]},
            "tilt": slide.get("tilt"),
            "offset": slide.get("offset"),
            "device": {
                "frameUrl": url_for(SKILL_DIR / "devices" / device["frame"]),
                "maskUrl": url_for(SKILL_DIR / "devices" / device["mask"]),
                "silhouetteUrl": url_for(SKILL_DIR / "devices" / device["silhouette"]) if device.get("silhouette") else None,
                "frameSize": device["frameSize"],
                "screen": device["screen"],
                "bleed": device.get("bleed", 0),
                "rim": device.get("rim"),
                "cameraCenterY": device.get("cameraCenterY"),
                "statusBarCenterY": device.get("statusBarCenterY"),
                "statusBarUnit": device.get("statusBarUnit"),
            },
            "screen": {
                "simUrl": url_for(sim_path),
                "simW": sim_w,
                "simH": sim_h,
                "edgeColor": edge_color(arr),
                "statusBar": device.get("statusBar"),
                "statusBarStyle": merged(DEFAULT_STATUS_BAR, self.set.get("statusBar"), loc.get("statusBar")),
            },
            "breakouts": breakouts,
            "vacate": vacate,
            "props": props,
        }


def serve(route, roots):
    path = Path(unquote(urlparse(route.request.url).path)[len("/f"):])
    if path.is_file() and any(path.is_relative_to(r) for r in roots):
        route.fulfill(path=str(path))
    else:
        route.fulfill(status=404, body=f"not found: {path}")


def run_slide(page, cfg):
    page.goto(url_for(SKILL_DIR / "layouts" / "canvas.html"))
    return page.evaluate("cfg => window.renderSlide(cfg)", cfg)


def save_jpeg(png_bytes, dst, size, quality):
    from io import BytesIO
    im = Image.open(BytesIO(png_bytes)).convert("RGB")
    if im.size != tuple(size):
        raise SystemExit(f"{dst}: rendered {im.size}, expected {tuple(size)}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, quality=quality, subsampling=0, optimize=True)


def label_font(size):
    for name in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def sheet(rows, dst, tile_h=900, gap=24, label_w=0):
    """rows: list of (label, [paths]); writes a grid with one labelled row each."""
    thumbs = []
    for label, paths in rows:
        row = []
        for p in paths:
            im = Image.open(p).convert("RGB")
            row.append(im.resize((round(im.width * tile_h / im.height), tile_h), Image.LANCZOS))
        thumbs.append((label, row))
    width = label_w + max(sum(t.width for t in row) + gap * (len(row) + 1) for _, row in thumbs)
    height = len(thumbs) * (tile_h + gap) + gap
    out = Image.new("RGB", (width, height), (245, 245, 243))
    draw, font = ImageDraw.Draw(out), label_font(30)
    y = gap
    for label, row in thumbs:
        draw.text((gap, y + tile_h // 2 - 15), label, fill=(40, 40, 40), font=font)
        x = label_w + gap
        for t in row:
            out.paste(t, (x, y))
            x += t.width + gap
        y += tile_h + gap
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, quality=90)


def baseline_files(project, device_id, locale, slides):
    table = project.set.get("baseline", {}).get(device_id, {})
    if locale not in table:
        return None
    folder = project.path(table[locale])
    found = []
    for slide in slides:
        prefix = slide["id"].split("-")[0] + "-"
        match = sorted(p for p in folder.glob(f"{prefix}*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        if not match:
            return None
        found.append(match[0])
    return found


def emit_config(project, locale, device_id):
    """Renderer configs for every slide of one locale and device, the way render() builds them."""
    slides = []
    for slide in project.resolved_slides(locale, device_id):
        try:
            cfg = project.slide_config(locale, device_id, slide, None, "render")
            slides.append({"id": slide["id"], "cfg": cfg})
        except SystemExit as err:
            slides.append({"id": slide["id"], "error": str(err)})
    return {"locale": locale, "device": device_id, "canvas": project.devices[device_id]["canvas"], "slides": slides}


def main():
    global URL_PREFIX
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--locale", action="append")
    ap.add_argument("--device", action="append")
    ap.add_argument("--slide", action="append")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--quality", type=int, default=94)
    ap.add_argument("--strict", action="store_true", help="exit 1 when any layout check fails")
    ap.add_argument("--emit-config", action="store_true", help="print renderer configs as JSON, render nothing")
    ap.add_argument("--url-prefix", help="prefix for file URLs in emitted configs, followed by the absolute path")
    ap.add_argument("--state", help="JSON file, or - for stdin, with set and locales replacing the project files")
    args = ap.parse_args()

    state = None
    if args.state:
        state = json.load(sys.stdin) if args.state == "-" else load_json(args.state)
    project = Project(args.project, state)
    locales = args.locale or project.set["locales"]
    devices = args.device or project.set["devices"]
    slides = [s for s in project.set["slides"] if not args.slide or s["id"] in args.slide]
    for d in devices:
        if d not in project.devices:
            raise SystemExit(f"unknown device {d}; known: {', '.join(project.devices)}")
    for code in locales:
        if code not in project.locales:
            raise SystemExit(f"locale {code} is not in set.json locales: {', '.join(project.locales)}")

    if args.emit_config:
        if len(locales) != 1 or len(devices) != 1:
            raise SystemExit("--emit-config needs exactly one --locale and one --device")
        if args.url_prefix:
            URL_PREFIX = args.url_prefix
        json.dump(emit_config(project, locales[0], devices[0]), sys.stdout, ensure_ascii=False)
        return

    failures = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb"])
        for device_id in devices:
            device = project.devices[device_id]
            w, h = device["canvas"]
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            page.route(f"{ORIGIN}/**", lambda route: serve(route, [project.root, SKILL_DIR]))
            for locale in locales:
                # Sizes come from every slide, so a --slide re-render matches the rest of the set.
                everything = project.resolved_slides(locale, device_id)
                resolved = [s for s in everything if s["id"] in {x["id"] for x in slides}]
                measured = [run_slide(page, project.slide_config(locale, device_id, s, None, "measure")) for s in everything]
                sizes = {"verb": min(m["verb"] for m in measured), "desc": min(m["desc"] for m in measured)}
                outputs = []
                for slide in resolved:
                    cfg = project.slide_config(locale, device_id, slide, sizes, "render")
                    report = run_slide(page, cfg)
                    dst = project.root / "out" / locale / device_id / f"{slide['id']}.jpg"
                    save_jpeg(page.locator("#canvas").screenshot(type="png"), dst, device["canvas"], args.quality)
                    outputs.append(dst)
                    status = "ok" if not report["problems"] else "CHECK: " + "; ".join(report["problems"])
                    failures += bool(report["problems"])
                    print(f"{locale} {device_id} {slide['id']}: {dst.relative_to(project.root)} "
                          f"verb {sizes['verb']}px desc {sizes['desc']}px, {report['descLines']} subline lines, {status}")
                sheet([(f"{locale} {device_id}", outputs)], project.root / "out" / "_sheets" / f"{locale}-{device_id}.jpg")
                if args.compare:
                    base = baseline_files(project, device_id, locale, resolved)
                    if base:
                        sheet([("baseline", base), ("canvas", outputs)], project.root / "out" / "compare" / f"{locale}-{device_id}.jpg",
                              label_w=170)
                    else:
                        print(f"{locale} {device_id}: no baseline configured or files missing, skipped compare")
            page.close()
        browser.close()
    if failures and args.strict:
        sys.exit(1)


if __name__ == "__main__":
    main()
