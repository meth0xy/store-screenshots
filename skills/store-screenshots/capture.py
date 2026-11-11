#!/usr/bin/env python3
"""
Capture store screens from iOS simulators and Android emulators, per locale and device.

  capture.py PROJECT doctor                       check tools, devices and the app
  capture.py PROJECT setup                        create, boot and prepare the capture devices
  capture.py PROJECT run --locale de-DE           every device and screen in one language
  capture.py PROJECT run --locale de-DE --device phone --screen 01-plan
  capture.py PROJECT describe --device phone      list tappable elements with labels and frames
  capture.py PROJECT shutdown                     shut the capture devices down

PROJECT/capture.json describes the app, the devices and the screens (see SKILL.md and
templates/capture.json). Captures land in PROJECT/<device dir>/<locale>/<screen>.png, which
is where render.py reads them, and a contact sheet in PROJECT/out/_captures/<locale>.jpg.

Only devices named in capture.json are touched. iOS devices are dedicated simulators that
this script creates by name; Android devices are AVDs that it boots.

Tools: xcrun simctl (Xcode) for iOS, adb and emulator (Android SDK) for Android. Taps on
iOS need idb (`brew install facebook/fb/idb-companion && pip install fb-idb`); screens that
only use deep links do not.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

DEFAULT_STATUS_TIME = "9:41"


def sh(cmd, check=True, capture=True, timeout=None, input=None):
    res = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, input=input)
    if check and res.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{(res.stderr or res.stdout or '').strip()}")
    return res.stdout if capture else ""


def which(tool):
    found = shutil.which(tool)
    if not found and tool in ("adb", "emulator"):
        for root in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                     str(Path.home() / "Library/Android/sdk"), str(Path.home() / "Android/Sdk")):
            if root:
                sub = "platform-tools" if tool == "adb" else "emulator"
                cand = Path(root) / sub / tool
                if cand.exists():
                    return str(cand)
    return found


def resolve(value, keys):
    """{"byDevice": {...}} / {"byLocale": {...}} variants, same rules as render.py."""
    if isinstance(value, dict):
        for kind in ("byDevice", "byLocale"):
            if set(value) == {kind}:
                for key in keys + ["default"]:
                    if key in value[kind]:
                        return resolve(value[kind][key], keys)
                return None
        return {k: resolve(v, keys) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, keys) for v in value]
    return value


def screen_for(raw, keys):
    """A screen with top-level byDevice/byLocale blocks: the matching block is merged over the rest."""
    base = {k: v for k, v in raw.items() if k not in ("byDevice", "byLocale")}
    for kind in ("byDevice", "byLocale"):
        if kind in raw:
            base.update(resolve({kind: raw[kind]}, keys) or {})
    return resolve(base, keys)


def flat_region(path, region, min_std):
    """True when the region (fractions x, y, w, h) is nearly one colour: a skeleton, spinner or blank state."""
    im = Image.open(path).convert("L")
    w, h = im.size
    x, y, rw, rh = region
    crop = np.asarray(im.crop((int(x * w), int(y * h), int((x + rw) * w), int((y + rh) * h))), float)
    return crop.std() < min_std


def island_in_capture(path):
    """The iOS simulator sometimes bakes the Dynamic Island into a capture as a black pill."""
    a = np.asarray(Image.open(path).convert("L"))
    h, w = a.shape
    if h < w:
        return False
    band = a[int(0.008 * h):int(0.057 * h), int(0.33 * w):int(0.67 * w)]
    return (band < 40).mean() > 0.3


class IOS:
    platform = "ios"

    def __init__(self, key, conf, app):
        self.key, self.conf, self.app = key, conf, app
        self.name = conf.get("name") or f"Capture {key}"
        self.bundle = app.get("bundleId")
        self._udid = None

    @property
    def udid(self):
        if not self._udid:
            data = json.loads(sh(["xcrun", "simctl", "list", "devices", "-j"]))
            for runtime, devs in data["devices"].items():
                for d in devs:
                    if d["name"] == self.name and d.get("isAvailable", True):
                        self._udid = d["udid"]
            if not self._udid:
                raise SystemExit(f"{self.key}: no simulator named '{self.name}'. Run: capture.py PROJECT setup")
        return self._udid

    def exists(self):
        try:
            return bool(self.udid)
        except SystemExit:
            return False

    def booted(self):
        data = json.loads(sh(["xcrun", "simctl", "list", "devices", "-j"]))
        return any(d["udid"] == self.udid and d["state"] == "Booted" for devs in data["devices"].values() for d in devs)

    def setup(self, root):
        if not self.exists():
            dtype = self.conf.get("type") or raise_missing(self.key, "type", 'e.g. "iPhone 17 Pro"')
            args = ["xcrun", "simctl", "create", self.name, dtype]
            if self.conf.get("runtime"):
                args.append(self.conf["runtime"])
            self._udid = sh(args).strip()
            print(f"{self.key}: created '{self.name}' ({dtype})")
        self.boot()
        app_path = self.app.get("app")
        if app_path:
            sh(["xcrun", "simctl", "install", self.udid, str((root / app_path).resolve())])
            print(f"{self.key}: installed {app_path}")
        elif self.app.get("copyFrom"):
            src = sh(["xcrun", "simctl", "get_app_container", self.app["copyFrom"], self.bundle]).strip()
            sh(["xcrun", "simctl", "install", self.udid, src])
            print(f"{self.key}: installed {self.bundle} from simulator {self.app['copyFrom']}")
        if self.bundle:
            sh(["xcrun", "simctl", "privacy", self.udid, "grant", "all", self.bundle], check=False)
        sh(["xcrun", "simctl", "ui", self.udid, "appearance", self.conf.get("appearance", "light")], check=False)

    def boot(self):
        if not self.booted():
            sh(["xcrun", "simctl", "boot", self.udid], check=False)
        sh(["xcrun", "simctl", "bootstatus", self.udid, "-b"], timeout=600)

    def set_locale(self, locale, mode):
        lang, _, region = locale.partition("-")
        if mode == "launchArgs":
            self.locale_args = ["-AppleLanguages", f"({locale})", "-AppleLocale", f"{lang}_{region or lang.upper()}"]
            return
        self.locale_args = []
        spawn = ["xcrun", "simctl", "spawn", self.udid, "defaults", "write", "-g"]
        sh(spawn + ["AppleLanguages", "-array", locale])
        sh(spawn + ["AppleLocale", f"{lang}_{region or lang.upper()}"])
        if mode == "relaunch":
            # Foldables lose their pose on reboot; the app picks the language up on its next launch.
            return
        sh(["xcrun", "simctl", "shutdown", self.udid], check=False)
        self.boot()

    def prepare(self):
        sh(["xcrun", "simctl", "status_bar", self.udid, "override", "--time", DEFAULT_STATUS_TIME,
            "--batteryState", "charged", "--batteryLevel", "100", "--cellularBars", "4", "--wifiBars", "3"], check=False)
        sh(["xcrun", "simctl", "ui", self.udid, "appearance", self.conf.get("appearance", "light")], check=False)
        if shutil.which("idb"):
            sh(["idb", "connect", self.udid], check=False)

    def launch(self):
        sh(["xcrun", "simctl", "terminate", self.udid, self.bundle], check=False)
        sh(["xcrun", "simctl", "launch", self.udid, self.bundle] + getattr(self, "locale_args", []))

    def open(self, url):
        sh(["xcrun", "simctl", "openurl", self.udid, url])

    def display(self):
        """Display index for sims with two screens (iPhone Duo): the one whose size matches displaySize."""
        want = self.conf.get("displaySize")
        if not want:
            return None
        if not hasattr(self, "_display"):
            res = subprocess.run(["xcrun", "simctl", "io", self.udid, "screenshot", "/dev/null"], capture_output=True, text=True)
            w, h = want.lower().split("x")
            found = re.findall(r"--display=(\d+)[^\n]*?(\d+)x(\d+)", res.stderr + res.stdout)
            match = [d for d, fw, fh in found if {fw, fh} == {w, h}]
            self._display = match[0] if match else None
            if not self._display:
                print(f"  {self.key}: no display of {want} reported, using the default display")
        return self._display

    def screenshot(self, out):
        display = self.display()
        sh(["xcrun", "simctl", "io", self.udid, "screenshot", *([f"--display={display}"] if display else []), str(out)])
        rotate_capture(out, self.conf.get("rotate"))

    def elements(self):
        need_idb()
        return [{"label": e.get("AXLabel") or "", "id": e.get("AXUniqueId") or "", "type": e.get("type", ""),
                 "frame": [e["frame"]["x"], e["frame"]["y"], e["frame"]["width"], e["frame"]["height"]]}
                for e in json.loads(sh(["idb", "ui", "describe-all", "--udid", self.udid, "--json"]))]

    def screen_points(self):
        els = self.elements()
        app = next((e for e in els if e["type"] == "Application"), None)
        if app:
            return app["frame"][2], app["frame"][3]
        return max(e["frame"][0] + e["frame"][2] for e in els), max(e["frame"][1] + e["frame"][3] for e in els)

    def tap_point(self, x, y):
        need_idb()
        sh(["idb", "ui", "tap", "--udid", self.udid, str(round(x)), str(round(y))])

    def type_text(self, text):
        need_idb()
        for i in range(0, len(text), 4):
            sh(["idb", "ui", "text", "--udid", self.udid, text[i:i + 4]])

    def press(self, name):
        need_idb()
        codes = {"enter": "40", "back": None, "home": None}
        if name == "home":
            sh(["idb", "ui", "button", "--udid", self.udid, "HOME"])
        elif codes.get(name):
            sh(["idb", "ui", "key", "--udid", self.udid, codes[name]])

    def swipe(self, x0, y0, x1, y1):
        need_idb()
        sh(["idb", "ui", "swipe", "--udid", self.udid, *(str(round(v)) for v in (x0, y0, x1, y1))])

    def shutdown(self):
        if self.exists():
            sh(["xcrun", "simctl", "shutdown", self.udid], check=False)


class Android:
    platform = "android"

    def __init__(self, key, conf, app):
        self.key, self.conf, self.app = key, conf, app
        self.avd = conf.get("avd") or raise_missing(key, "avd", "name from `emulator -list-avds`")
        self.package = app.get("package")
        self.adb = which("adb") or "adb"
        self.emulator = which("emulator") or "emulator"
        self._serial = conf.get("serial")

    def serials(self):
        out = sh([self.adb, "devices"], check=False)
        return [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]

    def find_serial(self):
        for s in self.serials():
            if not s.startswith("emulator-"):
                continue
            name = sh([self.adb, "-s", s, "emu", "avd", "name"], check=False).splitlines()
            if name and name[0].strip() == self.avd:
                return s
        return None

    @property
    def serial(self):
        if not self._serial:
            self._serial = self.find_serial()
            if not self._serial:
                raise SystemExit(f"{self.key}: emulator '{self.avd}' is not running. Run: capture.py PROJECT setup")
        return self._serial

    def a(self, *args, check=True):
        return sh([self.adb, "-s", self.serial, *args], check=check)

    def exists(self):
        return self.avd in sh([self.emulator, "-list-avds"], check=False).split()

    def booted(self):
        return bool(self.find_serial())

    def boot(self):
        if not self.booted():
            if not self.exists():
                raise SystemExit(f"{self.key}: no AVD named '{self.avd}'. Create it in Android Studio or with avdmanager")
            subprocess.Popen([self.emulator, "-avd", self.avd, "-no-snapshot-save", "-no-boot-anim", "-no-audio"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            for _ in range(180):
                time.sleep(2)
                if self.find_serial():
                    break
            else:
                raise SystemExit(f"{self.key}: emulator '{self.avd}' did not come up")
        self._serial = self.find_serial()
        for _ in range(180):
            if self.a("shell", "getprop", "sys.boot_completed", check=False).strip() == "1":
                return
            time.sleep(2)
        raise SystemExit(f"{self.key}: emulator '{self.avd}' did not finish booting")

    def setup(self, root):
        self.boot()
        apk = self.app.get("apk")
        if apk:
            self.a("install", "-r", "-g", str((root / apk).resolve()))
            print(f"{self.key}: installed {apk}")
        self.a("shell", "cmd", "uimode", "night", "no" if self.conf.get("appearance", "light") == "light" else "yes",
               check=False)

    def set_locale(self, locale, mode):
        if mode == "system":
            self.a("root", check=False)
            time.sleep(2)
            self.a("shell", "setprop", "persist.sys.locale", locale)
            self.a("shell", "setprop", "ctl.restart", "zygote")
            time.sleep(5)
            self.boot()
            return
        # Per-app language, Android 13+. The app must declare its locales (localeConfig) to honour it.
        self.a("shell", "cmd", "locale", "set-app-locales", self.package, "--locales", locale)

    def prepare(self):
        demo = lambda *extra: self.a("shell", "am", "broadcast", "-a", "com.android.systemui.demo", *extra, check=False)
        self.a("shell", "settings", "put", "global", "sysui_demo_allowed", "1", check=False)
        demo("-e", "command", "enter")
        demo("-e", "command", "clock", "-e", "hhmm", DEFAULT_STATUS_TIME.replace(":", "").zfill(4))
        demo("-e", "command", "battery", "-e", "level", "100", "-e", "plugged", "false")
        demo("-e", "command", "network", "-e", "wifi", "show", "-e", "level", "4")
        demo("-e", "command", "network", "-e", "mobile", "show", "-e", "level", "4", "-e", "datatype", "none")
        demo("-e", "command", "notifications", "-e", "visible", "false")

    def launch(self):
        self.a("shell", "am", "force-stop", self.package, check=False)
        activity = self.app.get("activity")
        if activity:
            self.a("shell", "am", "start", "-n", f"{self.package}/{activity}")
        else:
            self.a("shell", "monkey", "-p", self.package, "-c", "android.intent.category.LAUNCHER", "1")

    def open(self, url):
        args = ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url]
        if self.package:
            args.append(self.package)
        self.a(*args)

    def screenshot(self, out):
        data = subprocess.run([self.adb, "-s", self.serial, "exec-out", "screencap", "-p"], capture_output=True, check=True).stdout
        Path(out).write_bytes(data)
        rotate_capture(out, self.conf.get("rotate"))

    def elements(self):
        self.a("shell", "uiautomator", "dump", "/sdcard/ui.xml")
        xml = self.a("shell", "cat", "/sdcard/ui.xml")
        out = []
        for node in ET.fromstring(xml[xml.index("<"):]).iter("node"):
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
            if not m:
                continue
            x0, y0, x1, y1 = map(int, m.groups())
            out.append({"label": node.get("text") or node.get("content-desc") or "", "id": node.get("resource-id") or "",
                        "type": node.get("class", "").split(".")[-1], "frame": [x0, y0, x1 - x0, y1 - y0]})
        return out

    def screen_points(self):
        m = re.search(r"(\d+)x(\d+)", self.a("shell", "wm", "size").splitlines()[-1])
        return int(m.group(1)), int(m.group(2))

    def tap_point(self, x, y):
        self.a("shell", "input", "tap", str(round(x)), str(round(y)))

    def type_text(self, text):
        self.a("shell", "input", "text", text.replace(" ", "%s"))

    def press(self, name):
        self.a("shell", "input", "keyevent", {"enter": "66", "back": "4", "home": "3"}[name])

    def swipe(self, x0, y0, x1, y1):
        self.a("shell", "input", "swipe", *(str(round(v)) for v in (x0, y0, x1, y1)), "300")

    def shutdown(self):
        if self.booted():
            self.a("emu", "kill", check=False)


def rotate_capture(out, degrees):
    if degrees:
        Image.open(out).rotate(degrees, expand=True).save(out)


def raise_missing(key, field, hint):
    raise SystemExit(f"capture.json devices.{key} needs \"{field}\" ({hint})")


def need_idb():
    if not shutil.which("idb"):
        raise SystemExit("this step needs idb on iOS: brew install facebook/fb/idb-companion && pip install fb-idb. "
                         "Or replace the tap with a deep link.")


def find_element(dev, match):
    els = dev.elements()
    for e in els:
        if ("label" in match and e["label"] == match["label"]) or ("id" in match and e["id"] == match["id"]):
            return e
    for e in els:
        if "label" in match and match["label"].lower() in e["label"].lower():
            return e
    raise SystemExit(f"{dev.key}: no element matching {match}. See: capture.py PROJECT describe --device {dev.key}")


def run_step(dev, step):
    if "open" in step:
        dev.open(step["open"])
    elif "tap" in step:
        target = step["tap"]
        if "at" in target:
            w, h = dev.screen_points()
            dev.tap_point(target["at"][0] * w, target["at"][1] * h)
        else:
            e = find_element(dev, target)
            x, y, w, h = e["frame"]
            dev.tap_point(x + w / 2, y + h / 2)
    elif "type" in step:
        dev.type_text(step["type"])
    elif "key" in step:
        dev.press(step["key"])
    elif "swipe" in step:
        w, h = dev.screen_points()
        x0, y0, x1, y1 = step["swipe"]
        dev.swipe(x0 * w, y0 * h, x1 * w, y1 * h)
    elif "launch" in step:
        dev.launch()
    elif "wait" not in step:
        raise SystemExit(f"unknown step {step}")
    time.sleep(step.get("wait", 2 if "open" in step or "launch" in step else 1))


def take(dev, out, screen):
    ready = screen.get("ready")
    tries = (ready or {}).get("tries", 5)
    for attempt in range(max(tries, 3)):
        dev.screenshot(out)
        problems = []
        if dev.platform == "ios" and screen.get("retakeIsland", True) and island_in_capture(out):
            problems.append("Dynamic Island in capture")
        if ready and flat_region(out, ready["region"], ready.get("minStd", 20)):
            problems.append("ready region still flat")
        if not problems:
            return True
        time.sleep((ready or {}).get("delay", 2))
    print(f"  {out.name}: {', '.join(problems)} after {attempt + 1} tries, check it by eye")
    return False


def contact_sheet(root, locale, folders, screens):
    rows = []
    for folder in folders:
        files = [root / folder / locale / f"{s['id']}.png" for s in screens]
        files = [f for f in files if f.exists()]
        if files:
            rows.append(files)
    if not rows:
        return None
    th = 640
    thumbs = [[(lambda im: im.resize((round(im.width * th / im.height), th), Image.LANCZOS))(Image.open(f).convert("RGB"))
               for f in row] for row in rows]
    W = max(sum(t.width + 16 for t in row) for row in thumbs) + 16
    sheet = Image.new("RGB", (W, len(thumbs) * (th + 16) + 16), (245, 245, 243))
    y = 16
    for row in thumbs:
        x = 16
        for t in row:
            sheet.paste(t, (x, y))
            x += t.width + 16
        y += th + 16
    out = root / "out" / "_captures" / f"{locale}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=85)
    return out


def load(root):
    path = root / "capture.json"
    if not path.exists():
        raise SystemExit(f"{path} is missing. Start from templates/capture.json in the skill folder.")
    conf = json.loads(path.read_text())
    devices = {}
    for key, d in conf["devices"].items():
        cls = IOS if d["platform"] == "ios" else Android if d["platform"] == "android" else None
        if not cls:
            raise SystemExit(f"devices.{key}.platform must be ios or android")
        devices[key] = cls(key, d, conf["app"].get(d["platform"], {}))
    return conf, devices


def doctor(root, conf, devices):
    ok = True
    def line(good, text):
        nonlocal ok
        ok &= good
        print(("ok   " if good else "FAIL ") + text)
    platforms = {d.platform for d in devices.values()}
    if "ios" in platforms:
        line(bool(shutil.which("xcrun")), "xcrun (Xcode command line tools)")
        steps = json.dumps(conf.get("screens", []))
        if '"tap"' in steps or '"type"' in steps or '"swipe"' in steps:
            line(bool(shutil.which("idb")), "idb, needed for tap/type/swipe steps on iOS")
    if "android" in platforms:
        line(bool(which("adb")), "adb (Android platform-tools)")
        line(bool(which("emulator")), "emulator (Android SDK)")
    for key, dev in devices.items():
        try:
            exists = dev.exists()
            line(exists, f"{key}: {'simulator ' + dev.name if dev.platform == 'ios' else 'AVD ' + dev.avd} exists")
            if exists:
                line(dev.booted(), f"{key}: booted")
        except (SystemExit, FileNotFoundError) as err:
            line(False, f"{key}: {err}")
    for platform, app in conf["app"].items():
        ident = app.get("bundleId") or app.get("package")
        line(bool(ident), f"app.{platform}: {'bundleId' if platform == 'ios' else 'package'} {ident or 'missing'}")
    sys.exit(0 if ok else 1)


def run(root, conf, devices, locale, only_devices, only_screens):
    mode = conf.get("locale", {})
    for key, dev in devices.items():
        if only_devices and key not in only_devices:
            continue
        keys = [key, dev.platform, dev.conf.get("set", "")]
        folder = root / dev.conf.get("dir", "sim") / locale
        folder.mkdir(parents=True, exist_ok=True)
        print(f"{key}: {locale}")
        dev.boot()
        dev.set_locale(locale, dev.conf.get("locale") or mode.get(dev.platform, "reboot" if dev.platform == "ios" else "app"))
        dev.prepare()
        launch = resolve(conf.get("launch", {}), keys) or {}
        if launch.get("url"):
            dev.open(launch["url"])
        else:
            dev.launch()
        time.sleep(launch.get("wait", 8))
        for step in launch.get("steps", []):
            run_step(dev, step)
        for raw in conf["screens"]:
            screen = screen_for(raw, keys + [locale])
            if only_screens and screen["id"] not in only_screens:
                continue
            if screen.get("devices") and key not in screen["devices"]:
                continue
            out = folder / f"{screen['id']}.png"
            if screen.get("copyFrom"):
                shutil.copyfile(folder / f"{screen['copyFrom']}.png", out)
                print(f"  {screen['id']}: copied from {screen['copyFrom']}")
                continue
            for step in screen.get("steps", []):
                run_step(dev, step)
            take(dev, out, screen)
            w, h = Image.open(out).size
            print(f"  {screen['id']}: {out.relative_to(root)} {w}x{h}")
            for step in screen.get("after", []):
                run_step(dev, step)
    folders = [d.conf.get("dir", "sim") for d in devices.values()]
    sheet = contact_sheet(root, locale, list(dict.fromkeys(folders)), conf["screens"])
    if sheet:
        print(f"sheet: {sheet}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("command", choices=["doctor", "setup", "run", "describe", "shutdown"])
    ap.add_argument("--locale")
    ap.add_argument("--device", action="append")
    ap.add_argument("--screen", action="append")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    conf, devices = load(root)
    for d in args.device or []:
        if d not in devices:
            raise SystemExit(f"unknown device {d}; capture.json has {', '.join(devices)}")
    chosen = {k: v for k, v in devices.items() if not args.device or k in args.device}

    if args.command == "doctor":
        doctor(root, conf, chosen)
    elif args.command == "setup":
        for dev in chosen.values():
            dev.setup(root)
        print("setup done. Sign in or seed demo data now if the screens need it; the devices keep that state.")
    elif args.command == "run":
        if not args.locale or not re.match(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,4})?$", args.locale):
            raise SystemExit("--locale like de-DE is required")
        run(root, conf, chosen, args.locale, args.device, args.screen)
    elif args.command == "describe":
        if len(chosen) != 1:
            raise SystemExit("describe needs exactly one --device")
        dev = next(iter(chosen.values()))
        for e in dev.elements():
            if e["label"] or e["id"]:
                print(f"{e['type']:<18} {str([round(v) for v in e['frame']]):<24} {e['label'][:50]!r} {e['id']}")
    elif args.command == "shutdown":
        for dev in chosen.values():
            dev.shutdown()


if __name__ == "__main__":
    main()
