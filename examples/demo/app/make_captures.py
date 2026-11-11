#!/usr/bin/env python3
"""
Stand-in for capture.py in the demo: renders the fictional Fernly screens with Playwright at
simulator resolutions, so the example ships without a real app. Prints the pixel rect of
every [data-rect] element, which is what set.json breakouts point at.

  python make_captures.py            writes ../sim, ../sim-tablet, ../sim-duo
"""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
LOCALES = {"en-US": "en", "de-DE": "de"}
SCREENS = {"01-water": "today", "02-care": "detail", "03-plan": "schedule"}
SETS = {
    "sim": ("phone", 402, 874, 3),
    "sim-tablet": ("tablet", 1032, 1376, 2),
    "sim-duo": ("duo", 951, 669, 3),
}


def main():
    rects = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for folder, (layout, w, h, dpr) in SETS.items():
            page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=dpr)
            for locale, lang in LOCALES.items():
                out = PROJECT / folder / locale
                out.mkdir(parents=True, exist_ok=True)
                for slide, screen in SCREENS.items():
                    page.goto(f"{(HERE / 'screens.html').as_uri()}?screen={screen}&lang={lang}&layout={layout}")
                    page.wait_for_timeout(150)
                    page.screenshot(path=str(out / f"{slide}.png"))
                    found = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('[data-rect]')]
                        .map(e => { const r = e.getBoundingClientRect(); return [e.dataset.rect, [r.x, r.y, r.width, r.height]]; }))""")
                    for name, r in found.items():
                        rects[f"{folder} {locale} {slide} {name}"] = [round(v * dpr) for v in r]
            page.close()
        browser.close()
    print(json.dumps(rects, indent=1))


if __name__ == "__main__":
    main()
