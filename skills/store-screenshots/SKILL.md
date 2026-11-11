---
name: store-screenshots
description: End-to-end App Store and Google Play screenshot sets. Finds the app's benefits in the codebase, captures real screens from iOS simulators and Android emulators (iPhone, iPad, iPhone Duo, Android phone and tablet) in every language, composes each tile on an HTML canvas with device frames, headlines and callouts from locale JSON files, and renders every locale x device with Playwright. Use to create a store screenshot set, add a language, add a device size, or rebuild a set where text and UI must stay exact.
user-invocable: true
---

You build store screenshot sets as data, not as generated images. Each tile is an HTML
canvas with layers: background, headline, a device frame holding a real capture, and
breakouts (crops of the capture or vector callouts) lifted out of the screen in 3D. Every
string lives in `locales/<locale>.json`, so a new language is one JSON file plus captures,
and the render is deterministic: the same input gives the same pixels.

`$S` below is the folder this SKILL.md lives in, `$PY` is `$S/.venv/bin/python`. If the venv
is missing, create it once:

```bash
python3 -m venv $S/.venv && $S/.venv/bin/pip install -r $S/requirements.txt && $S/.venv/bin/playwright install chromium
```

Pipeline:

```
1 benefits -> 2 slides -> 3 capture.json -> 4 capture (simulators) -> 5 composition
-> 6 background -> 7 set.json + locales -> 8 render -> 9 review -> (more languages, devices)
```

`$S/../../examples/demo/` in the repository is a complete project (fictional app, two
languages, five devices). Read its `set.json` before writing one.

## Project layout

```
<project>/
  capture.json                  app, capture devices, screens to capture
  set.json                      composition shared by every locale
  locales/<locale>.json         every string: verb, subline, callout texts
  sim/<locale>/<slide>.png      phone captures (one folder per capture set)
  sim-tablet/  sim-duo/  sim-android/
  fonts/  assets/               optional fonts, background image, props
  out/<locale>/<device>/        store files only
  out/_sheets/                  contact sheet per locale and device
  out/_captures/                contact sheet per locale after capturing
```

Suggest `store-screenshots/` at the app repo root. Add `out/` and `.cache/` to its .gitignore.

---

## 1. Benefits

Read the codebase: navigation (tabs, routes, screens), onboarding copy, paywall copy,
README, existing store listing. List the 5 to 8 things a user gets, ranked by how much they
would move an install. One benefit per slide.

Each slide headline is one short verb in caps plus a subline of at most two lines:
`PLAN / YOUR WEEK IN MINUTES`, `SAVE / ON EVERY SHOP`. The verb names the benefit, not the
feature. Show the list with the screen that proves each one and get an ok.

## 2. Slides

Id `NN-name`; the number is the store position. Pick the screen per slide and note the
state it needs (logged in, data present, a sheet open). Tilt image-heavy screens, keep
data-dense screens straight.

## 3. capture.json

Start from `$S/templates/capture.json`.

| Key | Meaning |
|---|---|
| `app.ios` | `bundleId`, and `app` (path to a simulator `.app` build) or `copyFrom` (UDID of a simulator that has it installed) |
| `app.android` | `package`, optional `activity`, optional `apk` to install |
| `devices.<key>` | `platform` `ios` or `android`, `dir` (capture folder), iOS: `name` (dedicated sim, created by setup) and `type` (`xcrun simctl list devicetypes`), optional `runtime`; Android: `avd` (`emulator -list-avds`) |
| device extras | `appearance` (`light`), `locale` (overrides the mode below), `displaySize` (`2853x2007` picks the iPhone Duo inner display), `rotate` (degrees, for a capture that comes out sideways) |
| `locale` | per platform. iOS `reboot` (default, most reliable), `relaunch` (no reboot, keeps a foldable's pose) or `launchArgs`. Android `app` (default, per-app language on Android 13+, needs the app's localeConfig) or `system` (needs a root-capable emulator image) |
| `launch` | `wait` seconds after launch, optional `url` to open instead of launching (Expo dev client URL), optional `steps` |
| `screens` | list of `{id, steps, ready, after, devices, copyFrom}`, plus `byDevice` / `byLocale` blocks merged over the screen |

Steps run in order and each waits (`wait`, default 1 to 2 s):

| Step | Effect |
|---|---|
| `{"open": "myapp://route"}` | deep link. Prefer this to taps: it works on every device and platform |
| `{"tap": {"label": "Save"}}` | taps the element with that accessibility label (or `{"id": ...}`), exact then substring match |
| `{"tap": {"at": [0.5, 0.9]}}` | taps at a fraction of the screen |
| `{"type": "pasta"}`, `{"key": "enter"}`, `{"key": "back"}` | text and keys |
| `{"swipe": [0.5, 0.8, 0.5, 0.3]}` | swipe, fractions |
| `{"launch": true}` | restart the app |

`ready: {"region": [x, y, w, h], "minStd": 20, "tries": 5, "delay": 3}` retakes while that
region is one flat colour (skeleton rows, a spinner, an image still loading). iPhone captures
are retaken automatically when the simulator bakes the Dynamic Island into them.

`copyFrom: "01-home"` reuses another capture on that device (a tablet showing two screens in
one split view). `devices: ["phone"]` limits a screen to some devices.

`capture.py PROJECT describe --device phone` prints tappable elements with labels and frames.

## 4. Capture

```bash
$PY $S/capture.py <project> doctor              # tools, devices, app ids
$PY $S/capture.py <project> setup               # create/boot dedicated sims, boot AVDs, install the app
$PY $S/capture.py <project> run --locale en-US  # every device and screen
$PY $S/capture.py <project> run --locale en-US --device phone --screen 03-search
```

- Capture devices are dedicated: iOS sims are created by name (`Capture iPhone` ...), Android
  uses the AVDs named in capture.json. Never point capture.json at the simulator the user
  works in, and ask before booting devices or switching their language.
- After `setup`, the app usually needs a demo state: sign in to a demo account, seed data,
  dismiss first-run dialogs (save-password, notifications, review prompts). Do it once per
  device; the state survives language switches. Ask the user for demo credentials, never
  write them into capture.json.
- A dev build that shows debug overlays (dev menu buttons, toasts) needs those hidden for
  the capture session. Patch them out locally and do not commit the patch.
- Review `out/_captures/<locale>.jpg` tile by tile: no string in another language, no dialog,
  toast, spinner or empty state. Fix data or steps and rerun that screen. The renderer shows
  the capture as is; nothing paints over a mistake.
- iPhone Duo: unfold the sim and put it in landscape in Simulator or DeviceHub first; simctl
  cannot fold or rotate. Use deep links (HID taps may not reach the inner display),
  `"locale": "relaunch"` and `"displaySize": "2853x2007"`.

Cross-platform apps (React Native, Flutter) can skip Android capture: Android tiles reuse the
iPhone and iPad captures, which is what the default devices do. Native Android apps capture
their own screens into `sim-android/`, see section 10.

## 5. Composition (user approves before any render)

Sketch each slide in ASCII: headline, device position and tilt, which element breaks out and
where it lands. Get an ok, then write set.json.

```
 +------------------------------+
 |            WATER             |  verb, one line
 |      EVERY PLANT RIGHT       |  subline, max 2 lines
 |          ON TIME             |
 |      .--------------.        |
 |  [* 3 plants today]          |  callout, lifted, pokes out left
 |      |  Today ...   |        |  device tilted y 15, x 4
 +------------------------------+
```

## 6. Background

One background per set, shared by every locale and device. Either CSS
(`"background": {"css": "radial-gradient(...), #1f4a37"}`, the demo uses this) or an image
(`{"image": "assets/bg.png"}`, background-size cover). An image generator works well for a
soft plate; prompt for a seamless 9:16 studio backdrop in the brand colour with soft light,
and state explicitly: no objects, no text, no devices, no shapes. Generate two, the user
picks. Props (stickers, product shots) are PNGs with alpha placed per slide.

## 7. set.json and locales

Measure every rect in capture pixels with measure.py, never by eye:

```bash
$PY $S/measure.py sim/en-US/05-fill.png --at 700,790            # flood-fills a button, chip, pill
$PY $S/measure.py sim/en-US/01-plan.png --ink 40,415,700,75     # text bounds on a plain area
$PY $S/measure.py sim/en-US/02-cook.png --grid 440,1510,340,110  # zoomed grid image in the temp dir
```

| Key | Meaning |
|---|---|
| `locales`, `devices` | what a full render covers; device ids from `$S/devices/devices.json` |
| `background` | `{"image": ...}` or `{"css": ...}`, optional `position` |
| `fonts` | `{"family": "fonts/File.otf"}`, paths relative to the project; default `display` is the bundled Archivo Black |
| `headline` | `font`, `descFont`, `verbColor`, `descColor`, `shadow`, `verbTracking`, `descTracking`, `descWordSpacing` |
| `capture` | `platform`, `statusBarBand` (capture rows blended away, iOS default `[66, 128]`), `sets` (`{"tablet": {"dir": "sim-tablet", "statusBarBand": [12, 56]}}`), `devices` (`{"android-phone": "android"}` to point a device at another capture set) |
| `style` | `gloss`, `deviceShadow`, `thicknessRatio` (side wall depth), `sideLayers` |
| `statusBar` | `time`, `color` of the status bar the renderer draws |
| `layout` | per device id, overrides the devices.json layout (`deviceTop`, `deviceWidth`, `verbMax` ...) |
| `baseline` | `{device: {locale: folder}}` of a live set, for `--compare` |
| `slides` | list, below |

Slide: `id`, `sim` (capture file if not `<id>.png`), `tilt` `{x, y, z}` degrees, `offset`
`{x, y}` canvas fractions, `layout`, `breakouts`, `props` (`src`, `x`, `y`, `w` canvas
fractions, `rotate`, `shadow`, `z`: `front` or `back`).

Breakout:

| Key | Meaning |
|---|---|
| `type` | `crop`: the capture's own pixels; `callout`: vector pill with locale text |
| `rect` or `anchor` | `[x, y, w, h]` in capture px of the element it comes from |
| `at` or `dx`/`dy` | landing position (`at` = new top-left in capture px) or offset |
| `scale`, `lift` | size and z lift in px (default 80) |
| `vacate`, `vacatePad` | fill the original spot with the surrounding colour (moved, not copied) |
| `radius`, `shadow`, `origin` | corner radius in capture px, shadow strength, transform origin |
| `text`, `icon`, `style` | callout: key into the locale's `callouts`, `tag` or `sparkles`, and `font`, `size`, `bg`, `fg`, `padX`, `padY`, `gap`, `radius`, `weight`, `tracking`, `iconScale`, `iconColor` |

Any value inside `slides` can vary per device and per locale:
`{"byDevice": {"phone": ..., "tablet": ..., "default": ...}}` (keys: device id, capture set,
`default`) and `{"byLocale": {"de-DE": ..., "default": ...}}`, nested either way. Rects
nearly always vary by locale, because the same chip is wider in German.

Crops stay sharp up to about 1.7x for large elements. Small chips and text become callouts,
which are vectors and stay sharp at any scale.

Locale file:

```json
{
  "captures": "en-US",
  "dir": "ltr",
  "fonts": {"display": "fonts/NotoSansJP-Black.otf"},
  "slides": {
    "01-plan": {"verb": "Plan", "desc": "Your week in minutes", "callouts": {"savings": "approx. €7.81 saved"}}
  }
}
```

Only `slides` is required. `captures` makes a locale reuse another locale's captures (en-GB
reading en-US). `headline` and `statusBar` override set.json. Archivo Black covers Latin only;
Cyrillic, Greek, CJK, Arabic and Hebrew need a `fonts` override, RTL needs `"dir": "rtl"`.

## 8. Render

```bash
$PY $S/render.py <project>                                  # everything
$PY $S/render.py <project> --locale de-DE --device iphone-67 --slide 01-plan
$PY $S/render.py <project> --compare                        # baseline vs new sheets
```

Headline sizes are shared across all slides of a locale and device, so a set reads as one.
The renderer stops when a capture's aspect ratio does not match the device's screen opening.

## 9. Review

Open `out/_sheets/<locale>-<device>.jpg` and zoom into every breakout. Lines printed as
`CHECK` name the problem (headline at minimum size, overlap). A headline at minimum size means
the copy is too long: shorten it, do not lower the limits. Show the sheets to the user.

## 10. More languages and devices

Add a language:

```bash
$PY $S/add_locale.py <project> es-ES init --from en-US     # locale file, byLocale placeholders, folders
$PY $S/capture.py <project> run --locale es-ES             # captures
#   translate locales/es-ES.json: store copy, short verbs, callouts matching the capture
$PY $S/add_locale.py <project> es-ES measure --from en-US  # re-finds rects, review the side-by-side image
$PY $S/add_locale.py <project> es-ES measure --from en-US --write
$PY $S/add_locale.py <project> es-ES check                 # 0 errors before rendering
$PY $S/render.py <project> --locale es-ES
```

A regional variant (en-GB, de-AT, fr-CA): `init --share-captures`, then only texts change.

Devices in `$S/devices/devices.json`:

| id | canvas | reads | store slot |
|---|---|---|---|
| `iphone-67` | 1290x2796 | phone (1206x2622) | ASC `APP_IPHONE_67` |
| `android-phone` | 1080x1920 | phone | Play phone screenshots |
| `ipad-13` | 2064x2752 | tablet (2064x2752) | ASC `APP_IPAD_PRO_3GEN_129` |
| `android-tablet` | 1792x2400 | tablet | Play 7" and 10" tablet |
| `iphone-duo` | 2853x2007 | duo (2853x2007) | ASC iPhone Duo |

The frames are generic drawings made with `make_frame.py`, free to redistribute. A capture
size without a matching frame gets one:

```bash
# native Android captures, 1080x2400, into sim-android/
$PY $S/make_frame.py --style android --screen 1080x2400 --device android-phone --captures android
# then in set.json: "capture": {"sets": {"android": {"dir": "sim-android", "statusBarBand": [1, 70]}}}
```

Styles: `ios`, `android`, `tablet`, `landscape`. `--canvas WxH` sets the store size for a new
device id. Measure `statusBarBand` on a real capture: the rows of the status bar.

Official bezels look more exact. Apple's are in Apple Design Resources (Product Bezels) and
Google's in the Android Device Art Generator. Their licences allow use in your marketing but
not redistribution, so keep them out of public repos. Download one with a transparent screen,
put it in `$S/devices/`, run `$PY $S/prepare_device.py $S/devices/<frame>.png` and copy the
printed `frameSize`, `screen`, `bleed`, `mask` and `silhouette` into the device entry.

## 11. Upload

Only on an explicit instruction. `out/<locale>/<device>/` maps 1:1 onto a store slot, locale
codes as in the stores; files sort by slide id. Upload with the store APIs (App Store
Connect `appScreenshotSets`, Play Developer API `edits.images`) or fastlane
`deliver` / `supply`. Nothing but store files goes into those folders.

---

## Known traps

- Headless Chromium leaves parts of a tilted screen unpainted when one 3D context holds
  dozens of large textures. The side wall is a stack of half-resolution silhouettes for that
  reason; do not raise `sideLayers` much.
- CSS `mask-image` reads alpha. A greyscale mask masks nothing; prepare_device writes RGBA.
- A vacated element leaves its anti-aliased outline unless the fill grows past the rect
  (`vacatePad`).
- Condensed display fonts with narrow spaces make two words read as one at thumbnail size;
  `descWordSpacing` (default 0.08em) fixes that.
- iOS simulator language switches need a reboot to reach every process; `relaunch` mode is
  faster but some frameworks cache the language until the next boot.
- Android per-app language (`cmd locale set-app-locales`) only works when the app declares
  its supported locales; otherwise use `"locale": {"android": "system"}` on a Google APIs image.
- `simctl status_bar override` and Android demo mode clean the status bar, but the renderer
  blends the band away and draws its own anyway, so both are cosmetic for the captures.
