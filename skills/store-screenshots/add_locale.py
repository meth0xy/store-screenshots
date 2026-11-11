#!/usr/bin/env python3
"""
Add a store language to a canvas project in three steps.

  add_locale.py PROJECT es-ES init --from en-US        locale file, set.json entries, capture folders
  add_locale.py PROJECT es-ES measure --from en-US     re-find every per-locale rect in the new captures
  add_locale.py PROJECT es-ES measure --from en-US --write
  add_locale.py PROJECT es-ES check                    exit 1 if anything is missing before a render

init copies the source locale's texts (translate them afterwards) and its byLocale values
as placeholders. measure needs the captures: callout anchors are re-found with the same
flood fill as measure.py --at, crops by template matching against the source capture. It
writes $TMPDIR/add-locale-<locale>.jpg with source and new element side by side; look at it
before --write. Files are backed up to <project>/.cache/history/<timestamp>/ before writing.
"""

import argparse
import tempfile
import copy
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))
from measure import region_at  # noqa: E402


def load(path):
    return json.loads(Path(path).read_text())


def format_json(value):
    """Same layout as the admin editor: short flat arrays and objects on one line."""
    def prim(v):
        return v is None or not isinstance(v, (dict, list))

    def write(v, indent, lead):
        if prim(v):
            return json.dumps(v, ensure_ascii=False)
        items = v if isinstance(v, list) else list(v.values())
        if isinstance(v, list):
            parts = [write(x, indent + "  ", len(indent) + 2) for x in v]
        else:
            parts = [f"{json.dumps(k, ensure_ascii=False)}: {write(x, indent + '  ', len(indent) + 6 + len(k))}"
                     for k, x in v.items()]
        if not parts:
            return "[]" if isinstance(v, list) else "{}"
        flat = f"[{', '.join(parts)}]" if isinstance(v, list) else "{ " + ", ".join(parts) + " }"
        if all(prim(x) for x in items) and lead + len(flat) <= 96:
            return flat
        inner = indent + "  "
        body = ",\n".join(inner + p for p in parts)
        return f"[\n{body}\n{indent}]" if isinstance(v, list) else f"{{\n{body}\n{indent}}}"

    return write(value, "", 0) + "\n"


class Proj:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.set = load(self.root / "set.json")
        self.devices = load(SKILL_DIR / "devices" / "devices.json")

    def locale_file(self, code):
        return self.root / "locales" / f"{code}.json"

    def locale(self, code):
        return load(self.locale_file(code))

    def capture_sets(self):
        """{set name: folder} for every capture set the project's devices read."""
        sets = {}
        conf = self.set.get("capture", {}).get("sets", {})
        for dev in self.set["devices"]:
            name = self.devices[dev].get("captures", "phone")
            sets[name] = "sim" if name == "phone" else conf[name]["dir"]
        return sets

    def set_for_key(self, key):
        if key in ("phone", "default"):
            return "phone"
        if key in self.devices:
            return self.devices[key].get("captures", "phone")
        return key

    def capture_path(self, set_name, locale, slide):
        folder = self.capture_sets().get(set_name, "sim")
        return self.root / folder / locale / slide.get("sim", f"{slide['id']}.png")

    def backup(self, extra=()):
        base = self.root / ".cache" / "history" / time.strftime("%Y%m%d-%H%M%S")
        dst, n = base, 1
        while dst.exists():
            dst, n = base.with_name(f"{base.name}-{n}"), n + 1
        dst.mkdir(parents=True)
        shutil.copy2(self.root / "set.json", dst / "set.json")
        for f in extra:
            if f.exists():
                shutil.copy2(f, dst / f.name)
        return dst

    def save_set(self):
        (self.root / "set.json").write_text(format_json(self.set))


def by_locale_nodes(slide):
    """Yields (path, byLocale dict, capture-set key) for every per-locale value in a slide."""
    def walk(node, path, set_key):
        if isinstance(node, dict):
            if "byLocale" in node:
                yield path, node["byLocale"], set_key
                return
            if "byDevice" in node:
                for k, v in node["byDevice"].items():
                    yield from walk(v, f"{path}.byDevice.{k}", k)
                return
            for k, v in node.items():
                yield from walk(v, f"{path}.{k}", set_key)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}[{i}]", set_key)
    yield from walk(slide, slide["id"], "default")


def breakout_of(slide, path):
    marker = ".breakouts["
    if marker not in path:
        return None, None
    idx = int(path.split(marker, 1)[1].split("]", 1)[0])
    field = path.split("]", 1)[1].lstrip(".").split(".", 1)[0]
    return slide["breakouts"][idx], field


def cmd_init(p, code, src, share):
    if code in p.set["locales"]:
        raise SystemExit(f"{code} is already in set.json")
    if src not in p.set["locales"]:
        raise SystemExit(f"source {src} is not in set.json")
    out = p.locale_file(code)
    if out.exists():
        raise SystemExit(f"{out} exists; delete it or add the locale to set.json by hand")
    p.backup()
    loc = copy.deepcopy(p.locale(src))
    loc.pop("captures", None)
    if share:
        loc = {"captures": src, **loc}
    out.write_text(json.dumps(loc, ensure_ascii=False, indent=2) + "\n")

    p.set["locales"].append(code)
    added = 0
    if not share:
        for slide in p.set["slides"]:
            for _, node, _ in by_locale_nodes(slide):
                if src in node and code not in node:
                    node[code] = copy.deepcopy(node[src])
                    added += 1
        for folder in p.capture_sets().values():
            (p.root / folder / code).mkdir(parents=True, exist_ok=True)
    p.save_set()

    print(f"wrote {out.relative_to(p.root)} (copy of {src}, translate every verb, desc and callout)")
    print(f"set.json: locale added, {added} byLocale placeholders copied from {src}")
    if not share:
        print("captures go to: " + ", ".join(f"{f}/{code}/" for f in p.capture_sets().values()))
        print(f"then: add_locale.py {p.root} {code} measure --from {src}")


def find_pill(img, rect):
    x, y, w, h = rect
    probes = [(x + w // 2, y + h // 2), (x + min(h, w // 4), y + h // 2), (x + w - min(h, w // 4), y + h // 2)]
    best = None
    for px, py in probes:
        if not (0 <= px < img.width and 0 <= py < img.height):
            continue
        try:
            got, _ = region_at(img, px, py, 12)
        except SystemExit:
            continue
        dh = abs(got[3] - h)
        ok = dh <= max(4, h * 0.15) and abs(got[1] - y) <= max(8, h * 0.3) and got[2] >= h
        score = (not ok, dh + abs(got[1] - y))
        if best is None or score < best[0]:
            best = (score, got, ok)
    if best is None:
        return None, False
    return best[1], best[2]


def find_text(img, rect, tol=60):
    """Text without a pill: ink columns from the rect's left edge until a gap wider than the line height."""
    x, y, w, h = rect
    arr = np.asarray(img).astype(np.int16)
    H, W = arr.shape[:2]
    m = max(6, h // 4)
    top, bot = max(y - m, 0), min(y + h + m, H)
    above, below = arr[max(top - 8, 0):top], arr[bot:min(bot + 8, H)]
    if not len(above) or not len(below):
        return None, False
    bg = np.median(np.concatenate([above, below]), axis=0)
    ink = np.abs(arr[top:bot] - bg).sum(axis=2) > tol
    cols = ink.any(axis=0)
    start = next((c for c in range(max(x - h, 0), min(x + h, W)) if cols[c]), None)
    if start is None:
        return None, False
    end, gap = start, 0
    for c in range(start, W):
        if cols[c]:
            end, gap = c, 0
        else:
            gap += 1
            if gap > h:
                break
    rows = np.nonzero(ink[:, start:end + 1].any(axis=1))[0]
    got = [int(start), int(top + rows.min()), int(end - start + 1), int(rows.max() - rows.min() + 1)]
    return got, abs(got[3] - h) <= max(4, h * 0.2) and abs(got[0] - x) <= h


def find_crop(src_img, new_img, rect, step=4):
    """Best position of the source crop in the new capture, coarse then fine."""
    x, y, w, h = rect
    tpl = np.asarray(src_img.crop((x, y, x + w, y + h)).convert("L"), dtype=np.float32)
    big = np.asarray(new_img.convert("L"), dtype=np.float32)
    H, W = big.shape

    def ssd(cx, cy):
        win = big[cy:cy + h, cx:cx + w]
        return float(np.mean((win - tpl) ** 2)) if win.shape == tpl.shape else float("inf")

    small_t, small_b = tpl[::step, ::step], big[::step, ::step]
    th, tw = small_t.shape
    best = (float("inf"), x, y)
    for cy in range(0, small_b.shape[0] - th + 1):
        for cx in range(0, small_b.shape[1] - tw + 1):
            d = float(np.mean((small_b[cy:cy + th, cx:cx + tw] - small_t) ** 2))
            if d < best[0]:
                best = (d, cx * step, cy * step)
    _, bx, by = best
    fine = min(((ssd(cx, cy), cx, cy)
                for cy in range(max(by - step, 0), min(by + step, H - h) + 1)
                for cx in range(max(bx - step, 0), min(bx + step, W - w) + 1)), default=(float("inf"), x, y))
    rms = fine[0] ** 0.5
    return [fine[1], fine[2], w, h], rms < 10, rms


def cmd_measure(p, code, src, write):
    if code not in p.set["locales"]:
        raise SystemExit(f"{code} is not in set.json; run init first")
    results, tiles = [], []
    for slide in p.set["slides"]:
        for path, node, set_key in by_locale_nodes(slide):
            if src not in node:
                continue
            breakout, field = breakout_of(slide, path)
            value = node[src]
            if not (isinstance(value, list) and len(value) == 4 and breakout and field in ("rect", "anchor")):
                results.append((path, value, node.get(code), "manual (not a rect)"))
                continue
            set_name = p.set_for_key(set_key)
            src_path, new_path = p.capture_path(set_name, src, slide), p.capture_path(set_name, code, slide)
            if not new_path.exists():
                results.append((path, value, None, f"missing capture {new_path.relative_to(p.root)}"))
                continue
            src_img, new_img = Image.open(src_path).convert("RGB"), Image.open(new_path).convert("RGB")
            if breakout.get("type") == "callout":
                got, ok = find_pill(new_img, value)
                kind = "pill"
                if not ok:
                    text, text_ok = find_text(new_img, value)
                    if text_ok or not got:
                        got, ok, kind = text, text_ok, "text"
                note = f"ok ({kind})" if ok else "CHECK nothing pill- or text-shaped at the source spot"
            else:
                got, ok, rms = find_crop(src_img, new_img, value)
                note = (f"ok (crop, rms {rms:.0f})" if ok else
                        f"CHECK crop content differs (rms {rms:.0f}); pick the element with measure.py --at")
            if got and ok:
                node[code] = got
            results.append((path, value, got, note))
            tiles.append((path, src_img, value, new_img, got or value, ok))

    width = max((len(r[0]) for r in results), default=10)
    for path, old, new, note in results:
        print(f"{path:<{width}}  {src} {old}  ->  {code} {new}  {note}")

    if tiles:
        rows = []
        for path, si, sr, ni, nr, ok in tiles:
            pad = 40
            def cut(img, r):
                x, y, w, h = r
                box = (max(x - pad, 0), max(y - pad, 0), min(x + w + pad, img.width), min(y + h + pad, img.height))
                c = img.crop(box).copy()
                ImageDraw.Draw(c).rectangle((x - box[0], y - box[1], x - box[0] + w, y - box[1] + h),
                                            outline=(255, 0, 200) if ok else (255, 60, 0), width=4)
                c.thumbnail((600, 400))
                return c
            a, b = cut(si, sr), cut(ni, nr)
            row = Image.new("RGB", (1240, max(a.height, b.height) + 40), "white")
            ImageDraw.Draw(row).text((10, 8), f"{path}   {src} | {code}", fill=(0, 0, 0))
            row.paste(a, (10, 34))
            row.paste(b, (630, 34))
            rows.append(row)
        sheet = Image.new("RGB", (1240, sum(r.height for r in rows)), "white")
        yy = 0
        for r in rows:
            sheet.paste(r, (0, yy))
            yy += r.height
        out = Path(tempfile.gettempdir()) / f"add-locale-{code}.jpg"
        sheet.save(out, quality=85)
        print(f"review: {out}")

    if write:
        p.backup()
        p.save_set()
        print("set.json written; CHECK rows keep their placeholder, measure them by hand")
    else:
        print("dry run; add --write after the review")


def text_variants(value):
    """A callout text, or every device variant of one ({"byDevice": {...}})."""
    if isinstance(value, str):
        return [value]
    return [t for v in value.get("byDevice", {}).values() for t in text_variants(v)]


def cmd_check(p, code):
    errors, warnings = [], []
    if code not in p.set["locales"]:
        errors.append(f"{code} is not in set.json locales")
    if not p.locale_file(code).exists():
        raise SystemExit(f"missing {p.locale_file(code)}")
    loc = p.locale(code)
    cap_locale = loc.get("captures", code)
    others = {c: p.locale(c) for c in p.set["locales"] if c != code and p.locale_file(c).exists()}

    for slide in p.set["slides"]:
        sid = slide["id"]
        texts = loc.get("slides", {}).get(sid)
        if not texts:
            errors.append(f"{sid}: no texts in locales/{code}.json")
            continue
        for k in ("verb", "desc"):
            if not texts.get(k):
                errors.append(f"{sid}: {k} is empty")
        for b in slide.get("breakouts", []):
            if b.get("type") == "callout" and b.get("text") and not texts.get("callouts", {}).get(b["text"]):
                errors.append(f"{sid}: callout '{b['text']}' has no text")
        for other, ol in others.items():
            ot = ol.get("slides", {}).get(sid, {})
            if ol.get("captures") == code or loc.get("captures") == other:
                continue
            for k in ("verb", "desc"):
                if texts.get(k) and texts.get(k) == ot.get(k):
                    warnings.append(f"{sid}: {k} '{texts[k]}' equals {other}, translated?")
        callouts = [t for v in texts.get("callouts", {}).values() for t in text_variants(v)]
        all_text = " ".join([texts.get("verb", ""), texts.get("desc", "")] + callouts)
        if any(ord(ch) > 0x24F and ch not in "€’‘“”„«»–…•·" for ch in all_text) and "fonts" not in loc:
            warnings.append(f"{sid}: non-Latin characters; the default font may lack them, set fonts in the locale file")

        for set_name in p.capture_sets():
            f = p.capture_path(set_name, cap_locale, slide)
            if not f.exists():
                errors.append(f"{sid}: missing capture {f.relative_to(p.root)}")
                continue
            ref = next((p.capture_path(set_name, c, slide) for c in others
                        if p.capture_path(set_name, c, slide).exists()), None)
            if ref:
                a, b = Image.open(f).size, Image.open(ref).size
                if a != b:
                    errors.append(f"{sid}: {f.relative_to(p.root)} is {a[0]}x{a[1]}, other locales {b[0]}x{b[1]}")

        for path, node, _ in by_locale_nodes(slide):
            if not any(k in node for k in (code, cap_locale, "default")):
                errors.append(f"{path}: byLocale has no {code}")

    for w in warnings:
        print(f"warn   {w}")
    for e in errors:
        print(f"error  {e}")
    print(f"{code}: {len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("locale")
    ap.add_argument("step", choices=["init", "measure", "check"])
    ap.add_argument("--from", dest="src", default="en-US", help="locale to copy texts and rects from")
    ap.add_argument("--share-captures", action="store_true",
                    help="init only: read the source locale's captures (en-GB from en-US)")
    ap.add_argument("--write", action="store_true", help="measure only: save the found rects to set.json")
    args = ap.parse_args()
    p = Proj(args.project)
    if args.step == "init":
        cmd_init(p, args.locale, args.src, args.share_captures)
    elif args.step == "measure":
        cmd_measure(p, args.locale, args.src, args.write)
    else:
        cmd_check(p, args.locale)


if __name__ == "__main__":
    main()
