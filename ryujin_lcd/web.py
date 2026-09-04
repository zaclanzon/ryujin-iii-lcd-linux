"""Web control panel for the Ryujin III LCD, in the style of Armoury Crate.

  ryujin-lcd-web [--host 127.0.0.1] [--port 8686] [--demo] [--no-restore] [-v]
  ryujin-lcd-web --import-crate /mnt        thumbnails from Armoury Crate's copies, see import_crate()

Serves a single-page app (ryujin_lcd/static) and a JSON API from the Python standard
library, no framework. Everything the CLI does is available: display mode (hardware
monitor with live sensors, animation, wallpaper with banner text, clock), brightness,
standby, media upload with crop, delete, storage, raw commands.

All device access goes through one lock, so the page, the live hardware-monitor feed
and an upload never interleave on the shared HID interface. Uploaded media are kept
in $XDG_DATA_HOME/ryujin-lcd/media so the page can show thumbnails (the device
cannot read files back). The applied settings are saved in $XDG_CONFIG_HOME/ryujin-lcd/web.json.

At start the saved mode is re-applied where the device needs the host for it: the
live sensor feed is started again, the clock is set again (--no-restore skips this).
A stored animation or wallpaper keeps playing on its own.

--demo runs without the cooler: a simulated device with simulated sensors, so the
page can be tried (and developed) on any machine.

  GET  /api/status[?storage=1]     device, display, storage, monitor, saved config
  GET  /api/sensors                hwmon sensors with current values
  POST /api/display                {brightness, standby, anim_slot}
  POST /api/hwmon                  {lines:[{label, sensor|value}], bg, fg, live, interval}
  POST /api/show                   {source: gif|jpg|clock, slot, duration, h24, banner}
  POST /api/upload?type=&slot=&name=&crop=x,y,w,h[&raw=1]   body = file
  POST /api/thumbnail?type=&slot=&name=&crop=x,y,w,h         body = file; local copy only, nothing sent
  GET  /api/media/<type>/<slot>    cached 320x240 file (thumbnail)
  DELETE /api/media/<type>/<slot>
  POST /api/raw                    {hex: "DC"} -> {reply}
  POST /api/monitor/stop
"""
import argparse
import copy
import datetime
import functools
import glob
import io
import json
import math
import mimetypes
import os
import signal
import struct
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .device import (CMD, FTYPE, HEIGHT, KIND, MODE_HWMON, MODE_SLIDESHOW, MTYPE, REPLY_ID, WIDTH,
                     Ryujin, RyujinError, add_unit_glyphs, encode, hexs, trim,
                     xdg_config_home, xdg_data_home)
from .monitor import FORMATS, hwmon_id, read_value
from . import __version__

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DATA_DIR = os.path.join(xdg_data_home(), "ryujin-lcd")
CONFIG = os.path.join(xdg_config_home(), "ryujin-lcd", "web.json")
SLOTS = 16
MAX_UPLOAD = 16 << 20
EXT = {"gif": "gif", "jpg": "jpg"}
MAGIC = {"gif": b"GIF8", "jpg": b"\xFF\xD8"}
KIND_NAME = {0x10: "gif", 0x04: "jpg", 0x08: "clock", MODE_HWMON: "hwmon", MODE_SLIDESHOW: "slideshow"}

DEFAULT_CONFIG = {
    "mode": "hwmon",
    "hwmon": {
        "lines": [{"label": "Coolant", "sensor": "rog_ryujin/temp1"},
                  {"label": "Pump", "sensor": "rog_ryujin/fan1"},
                  {"label": "CPU", "sensor": "k10temp/temp1"}],
        "count": 3, "bg": "000000", "fg": "FFFFFF", "live": True, "interval": 2,
    },
    "slideshow": {
        "source": "gif", "gif_slots": [], "jpg_slot": None, "duration": 5, "h24": False,
        "banner": {"lines": ["", "", "", "", "", ""], "color": "FFFFFFFF", "align": 0, "x": 8, "font": 3},
    },
}


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# --- parsing of device replies ---------------------------------------------------
def parse_display_status(st):
    return {
        "brightness": st[7], "brightness_ac": st[12],
        "standby": bool(st[13]),
        "anim_type": st[14], "anim_slot": st[15],
        "media": {"kind": KIND_NAME.get(st[8], f"{st[8]:02X}"), "type": st[9], "slot": st[10]},
        "raw": hexs(trim(st)),
    }


def parse_disk_info(d):
    total, free = struct.unpack("<II", d[4:12])
    out = {"total_kb": total, "free_kb": free, "raw": hexs(trim(d))}
    for name, off in (("other", 12), ("jpg", 17), ("gif", 22)):
        cap, bits = d[off], struct.unpack("<I", d[off + 1:off + 5])[0]
        out[name] = {"capacity": cap, "used": [i for i in range(32) if bits >> i & 1]}
    return out


# --- demo device -------------------------------------------------------------------
class DemoRyujin:
    """Stands in for the cooler: the same methods as Ryujin, state kept in memory."""
    path = "demo"

    def __init__(self, verbose=False):
        self.verbose = verbose
        self.st = bytearray(64)
        self.st[0], self.st[1], self.st[4], self.st[7] = CMD, 0x5C, 100, 60
        self.st[8], self.st[9], self.st[10], self.st[12] = 0x10, 0x01, 0x04, 60
        self.st[13], self.st[14], self.st[15] = 0x10, 0x01, 0x04
        self.slots = {"gif": {4}, "jpg": set()}
        self.sizes = {("gif", 4): 24}
        self.total = 32424
        self.current = (MODE_HWMON, 0, 0)

    def firmware(self):
        return "AURJ2-S750-0108"

    def display_status(self):
        return bytearray(self.st)

    def set_display_status(self, **fields):
        idx = {"brightness": 7, "standby": 13, "anim_type": 14, "anim_slot": 15}
        for k, v in fields.items():
            if v is not None:
                self.st[idx[k]] = v
        if fields.get("brightness") is not None:
            self.st[12] = fields["brightness"]
        return self.display_status()

    def disk_info(self):
        d = bytearray(64)
        d[0], d[1], d[3] = CMD, 0x71, 0x01
        free = self.total - sum(self.sizes.values())
        d[4:12] = struct.pack("<II", self.total, free)
        for name, off in (("other", 12), ("jpg", 17), ("gif", 22)):
            d[off] = 0x10
            bits = sum(1 << s for s in self.slots.get(name, ()))
            d[off + 1:off + 5] = struct.pack("<I", bits)
        return d

    def upload(self, data, ftype, slot):
        for _ in range(0, len(data), 4096):
            time.sleep(0.03)
        self.slots[ftype].add(slot)
        self.sizes[(ftype, slot)] = max(8, -(-len(data) // 1024))

    def delete(self, ftype, slot):
        on_screen = (self.current == (KIND["gif"], MTYPE["gif"], slot) if ftype == "gif"
                     else self.current[0] == MODE_SLIDESHOW and self.current[2] == slot)
        if on_screen:
            return      # like the cooler: acknowledged, but the file stays while it is shown
        self.slots[ftype].discard(slot)
        self.sizes.pop((ftype, slot), None)

    def slideshow_list(self, entries, duration):
        pass

    def play(self, kind, slot):
        self.current = (KIND[kind], MTYPE[kind], slot)
        self.st[8], self.st[9], self.st[10] = self.current

    def mode(self, m):
        self.current = (m, 0, 0)

    def set_clock(self, now=None, h24=False):
        pass

    def hwmon(self, lines, layout=0, next_layout=None, bg=(0, 0, 0), fg=(255, 255, 255)):
        pass

    def hwmon_update(self, lines):
        pass

    def current_item(self):
        return self.current

    def banner(self, kind, slot, lines, font=3, align=0, color=(255, 255, 255, 255), duration=5, x=8):
        self.current = (MODE_SLIDESHOW, 0, slot)
        self.st[8], self.st[9], self.st[10] = KIND["jpg"], MTYPE["jpg"], slot

    def cmd(self, payload, timeout=3.0):
        payload = bytes(payload)
        if payload[0] == 0xDC:
            return bytes(self.st)
        if payload[0] == 0xF1:
            return bytes(self.disk_info())
        if payload[0] == 0x82:
            return bytes([CMD, 0x02, 0x00]) + b"AURJ2-S750-0108" + bytes(46)
        if payload[0] == 0xD0:
            return bytes([CMD, 0x50, 0x00, 0x01, 0x30, *self.current, 0x01]) + bytes(55)
        return bytes([CMD, REPLY_ID.get(payload[0], payload[0])]) + bytes(62)

    def close(self):
        pass


# --- sensors -------------------------------------------------------------------
DEMO_SENSORS = [   # id, label, base value (in hwmon raw units), swing
    ("rog_ryujin/temp1", "Coolant", 34500, 2500),
    ("rog_ryujin/fan1", "Pump", 1980, 120),
    ("rog_ryujin/fan2", "Radiator fans", 1240, 300),
    ("k10temp/temp1", "Tctl", 52000, 9000),
    ("k10temp/temp3", "Tccd1", 49000, 7000),
    ("nvme/temp1", "Composite", 41000, 2000),
    ("amdgpu/temp1", "edge", 47000, 8000),
    ("amdgpu/power1", "PPT", 118e6, 60e6),
    ("amdgpu/freq1", "sclk", 1850e6, 500e6),
    ("asus/in0", "Vcore", 1066, 120),
]
T0 = time.monotonic()


def demo_value(sid, swing_scale=1.0):
    for i, (s, _, base, swing) in enumerate(DEMO_SENSORS):
        if s == sid:
            t = time.monotonic() - T0
            return base + swing * swing_scale * math.sin(t / (7 + i * 1.3) + i)
    return None


class Sensors:
    def __init__(self, demo):
        self.demo = demo

    def real(self):
        out, seen = [], {}
        for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            try:
                name = open(os.path.join(d, "name")).read().strip()
            except OSError:
                continue
            seen.setdefault(name, []).append(d)
        dupes = {n for n, ds in seen.items() if len(ds) > 1}
        for name, dirs in seen.items():
            for d in dirs:
                out += self._device(d, hwmon_id(d) if name in dupes else name)
        return out

    @staticmethod
    def _device(d, name):
        out = []
        for inp in sorted(glob.glob(os.path.join(d, "*_input"))):
            attr = os.path.basename(inp)[:-6]
            kind = attr.rstrip("0123456789")
            if kind not in FORMATS:
                continue
            label = attr
            try:
                label = open(os.path.join(d, attr + "_label")).read().strip() or attr
            except OSError:
                pass
            out.append({"id": f"{name}/{attr}", "hwmon": name, "attr": attr, "label": label, "kind": kind})
        return out

    def list(self):
        items = self.real()
        if self.demo:
            have = {i["id"] for i in items}
            for sid, label, _, _ in DEMO_SENSORS:
                if sid not in have:
                    hw, attr = sid.split("/")
                    items.append({"id": sid, "hwmon": hw, "attr": attr, "label": label,
                                  "kind": attr.rstrip("0123456789"), "demo": True})
        for it in items:
            it["value"] = self.read(it["hwmon"], it["attr"])
        return items

    def read(self, hw, attr):
        kind = attr.rstrip("0123456789")
        fmt = FORMATS.get(kind)
        if fmt is None:
            return "n/a"
        v = read_value(hw, attr, fmt)
        if v == "n/a" and self.demo:
            raw = demo_value(f"{hw}/{attr}")
            if raw is not None:
                div, pat, unit = fmt
                return pat.format(raw / div) + unit
        return v


# --- device access -------------------------------------------------------------------
class Device:
    """Opens the cooler on first use, serializes all access, drops it on error."""

    def __init__(self, demo=False, verbose=False):
        self.demo, self.verbose = demo, verbose
        self.lock = threading.RLock()
        self.dev = None
        self.error = None

    def run(self, fn):
        with self.lock:
            try:
                if self.dev is None:
                    self.dev = DemoRyujin(self.verbose) if self.demo else Ryujin(self.verbose)
                r = fn(self.dev)
                self.error = None
                return r
            except (RyujinError, OSError) as e:
                self.error = str(e)
                self.drop()
                raise ApiError(str(e), 503)

    def drop(self):
        if self.dev is not None:
            try:
                self.dev.close()
            except OSError:
                pass
            self.dev = None


class LiveMonitor(threading.Thread):
    """Feeds the hardware-monitor page from sensors, like ryujin-lcd-monitor, in-process."""

    def __init__(self, device, sensors, lines, bg, fg, interval=2.0, keepalive=60.0):
        super().__init__(daemon=True, name="ryujin-live-monitor")
        self.device, self.sensors = device, sensors
        self.lines = [(l["label"], *l["sensor"].split("/", 1)) for l in lines]
        self.bg, self.fg = bg, fg
        self.interval, self.keepalive = max(0.5, float(interval)), keepalive
        self.stop_event = threading.Event()
        self.last = None
        self.error = None

    def page(self):
        return [(label, add_unit_glyphs(self.sensors.read(hw, attr))) for label, hw, attr in self.lines]

    def run(self):
        checked_at, sent = 0.0, None
        while not self.stop_event.is_set():
            page = self.page()
            now = time.monotonic()
            try:
                if sent is None or now - checked_at >= self.keepalive:
                    def full(dev):
                        if sent is None or dev.current_item()[0] != MODE_HWMON:
                            dev.hwmon(page, 0, None, self.bg, self.fg)
                            dev.mode(MODE_HWMON)
                        elif page != sent:
                            dev.hwmon_update(page)
                    self.device.run(full)
                    checked_at = now
                elif page != sent:
                    self.device.run(lambda dev: dev.hwmon_update(page))
                sent, self.last, self.error = page, page, None
            except ApiError as e:
                self.error = str(e)
                sent = None
                self.stop_event.wait(5)
                continue
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()


class SlideshowPlayer(threading.Thread):
    """Cycle several stored animations by playing each in turn, from the host.

    Armoury Crate never used the device's own multi-entry list (only n=1 was ever captured),
    so the rotation is driven here with playMedia, which are display commands, not flash writes.
    Each play reports its slot, so the panel shows what is on screen."""

    def __init__(self, device, slots, interval=5.0):
        super().__init__(daemon=True, name="ryujin-slideshow")
        self.device = device
        self.slots = list(slots)
        self.interval = max(1.0, float(interval))
        self.stop_event = threading.Event()
        self.current = None
        self.error = None

    def run(self):
        i, first = 0, True
        while not self.stop_event.is_set():
            slot = self.slots[i % len(self.slots)]
            try:
                def go(dev, slot=slot, first=first):
                    if first:
                        dev.slideshow_list([("gif", slot)], int(self.interval))
                    dev.play("gif", slot)
                    settle(dev, KIND["gif"])
                self.device.run(go)
                self.current, self.error = slot, None
            except ApiError as e:
                self.error = str(e)
                self.stop_event.wait(5)
                continue
            first = False
            i += 1
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()


# --- media cache and config -------------------------------------------------------------
def media_path(ftype, slot):
    return os.path.join(DATA_DIR, "media", f"{ftype}-{slot}.{EXT[ftype]}")


def media_meta(ftype, slot):
    try:
        with open(media_path(ftype, slot) + ".json") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_media(ftype, slot, data, name):
    os.makedirs(os.path.join(DATA_DIR, "media"), exist_ok=True)
    p = media_path(ftype, slot)
    with open(p, "wb") as f:
        f.write(data)
    with open(p + ".json", "w") as f:
        json.dump({"name": name, "bytes": len(data), "time": time.time()}, f)


def forget_media(ftype, slot):
    for p in (media_path(ftype, slot), media_path(ftype, slot) + ".json"):
        try:
            os.remove(p)
        except OSError:
            pass


def import_crate(root, storage=None):
    """Take over Armoury Crate's local copies of the media it uploaded, from a mounted
    system partition of the OS it ran on (read-only is fine). Armoury Crate cannot read
    files back either: it keeps every upload, already converted to 320x240, under
      <root>/Program Files (x86)/ASUS/ArmouryDevice/View/externalFiles/aio/RYUJIN_III/<id>.gif|jpg
    and its profile maps each id to a device slot:
      <root>/ProgramData/ASUS/Framework/aioFan/RYUJIN3/fp_1_config.xml
    (base64 of URL-encoded JSON; display.media.uploadImages[] = {id, category 0 gif / 1 jpg,
    ext, mediaIndex}). Verified 2026-09-03: the copy is byte-identical to what went over the
    bulk pipe. Returns [(ftype, slot, name, bytes, note)]; storage (from parse_disk_info)
    marks slots the device does not list as used."""
    import base64, re
    root = root.rstrip("/")
    profile = os.path.join(root, "ProgramData/ASUS/Framework/aioFan/RYUJIN3/fp_1_config.xml")
    media = os.path.join(root, "Program Files (x86)/ASUS/ArmouryDevice/View/externalFiles/aio")
    try:
        xml = open(profile, errors="replace").read()
    except OSError as e:
        raise ApiError(f"no Armoury Crate profile at {profile}: {e}")
    items = None
    for blob in re.findall(r"[A-Za-z0-9+/=]{200,}", xml):
        try:
            cfg = json.loads(urllib.parse.unquote(base64.b64decode(blob).decode("utf-8", "replace")))
            items = cfg["display"]["media"]["uploadImages"]
            break
        except (ValueError, KeyError, TypeError):
            continue
    if items is None:
        raise ApiError(f"{profile}: no uploadImages list found in the profile")
    dirs = [d for d in glob.glob(os.path.join(media, "RYUJIN_III*")) if os.path.isdir(d)]
    out = []
    for it in items:
        ftype = {"0": "gif", "1": "jpg"}.get(str(it.get("category")))
        if ftype is None:
            continue
        slot, name = int(it["mediaIndex"]), f"{it['id']}.{it.get('ext', ftype)}"
        src = next((os.path.join(d, name) for d in dirs if os.path.isfile(os.path.join(d, name))), None)
        if src is None:
            out.append((ftype, slot, name, 0, "file missing in the Armoury Crate folder")); continue
        data = open(src, "rb").read()
        note = ""
        if not data.startswith(MAGIC[ftype]):
            note = f"not a {ftype} file, skipped"
        elif storage and slot not in storage[ftype]["used"]:
            note = "slot is empty on the device, skipped"
        else:
            save_media(ftype, slot, data, f"armoury-crate-{ftype}-{slot}.{EXT[ftype]}")
            note = "local copy taken" + describe_image(data)
        out.append((ftype, slot, name, len(data), note))
    return out


def describe_image(data):
    """' (320x240, 13 frames)' - so a stale profile pointing at stand-in files shows up."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        n = getattr(im, "n_frames", 1)
        return f" ({im.width}x{im.height}, {n} frame{'s' if n != 1 else ''})"
    except Exception:  # noqa: BLE001
        return ""


def merge(base, upd):
    if not isinstance(upd, dict):
        raise ApiError("configuration must be an object")
    out = dict(base)
    for k, v in upd.items():
        out[k] = merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def validate_config(cfg):
    """Reject persisted data that cannot safely be consumed by restore or the UI."""
    if not isinstance(cfg, dict) or cfg.get("mode") not in ("hwmon", "slideshow"):
        raise ApiError("invalid display mode in configuration")
    hw = cfg.get("hwmon")
    ss = cfg.get("slideshow")
    if not isinstance(hw, dict) or not isinstance(ss, dict):
        raise ApiError("invalid configuration section")
    lines = hw.get("lines")
    if not isinstance(lines, list) or not all(isinstance(line, dict) for line in lines):
        raise ApiError("invalid hardware-monitor lines")
    if any("sensor" in line and not isinstance(line["sensor"], str) for line in lines):
        raise ApiError("invalid hardware-monitor sensor")
    rgb(hw.get("bg", "000000"))
    rgb(hw.get("fg", "FFFFFF"))
    parse_interval(hw.get("interval", 2))
    try:
        int(ss.get("duration"))
    except (TypeError, ValueError):
        raise ApiError("invalid slideshow duration")
    if ss.get("source") not in ("gif", "jpg", "clock"):
        raise ApiError("invalid slideshow source")
    if not isinstance(ss.get("gif_slots"), list) or not isinstance(ss.get("banner"), dict):
        raise ApiError("invalid slideshow configuration")
    return cfg


def load_config():
    try:
        with open(CONFIG) as f:
            return validate_config(merge(DEFAULT_CONFIG, json.load(f)))
    except (OSError, ValueError, TypeError, ApiError):
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg):
    validate_config(cfg)
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG)


def prepare_bytes(data, ftype, crop=None):
    """Crop (source pixels, optional), center-fit to 320x240, re-encode. Needs Pillow."""
    try:
        from PIL import Image
    except ImportError:
        raise ApiError("Pillow is not installed (sudo apt install python3-pil); only raw 320x240 files can be sent", 501)
    try:
        im = Image.open(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001 - Pillow raises many types
        raise ApiError(f"cannot decode image: {e}")

    def prep(fr):
        if crop:
            x, y, w, h = crop
            box = (max(0, int(round(x))), max(0, int(round(y))),
                   min(fr.width, int(round(x + w))), min(fr.height, int(round(y + h))))
            if box[2] - box[0] >= 2 and box[3] - box[1] >= 2:
                fr = fr.crop(box)
        return fr

    try:
        return encode(im, ftype, prep)
    except Exception as e:  # noqa: BLE001
        raise ApiError(f"cannot convert image: {e}")


def settle(dev, kind, timeout=1.0):
    """Wait until the current-item reply shows `kind`: the cooler answers with the previous
    page for ~100 ms after a play/mode command (measured 2026-09-03)."""
    end = time.monotonic() + timeout
    while dev.current_item()[0] != kind and time.monotonic() < end:
        time.sleep(0.05)


def rgb(h, n=3):
    try:
        b = bytes.fromhex(h.lstrip("#"))
    except (AttributeError, TypeError, ValueError):
        raise ApiError(f"bad color {h!r}")
    if len(b) == 3 and n == 4:
        b += b"\xFF"
    if len(b) != n:
        raise ApiError(f"color {h!r}: want {n} bytes")
    return tuple(b)


def parse_interval(value):
    try:
        interval = float(value)
    except (TypeError, ValueError):
        raise ApiError("interval must be a positive number")
    if not math.isfinite(interval) or interval <= 0:
        raise ApiError("interval must be a positive number")
    return interval


def check_slot(ftype, slot):
    if ftype not in FTYPE:
        raise ApiError("type must be gif or jpg")
    try:
        slot = int(slot)
    except (TypeError, ValueError):
        raise ApiError("slot must be a number")
    if not 0 <= slot < SLOTS:
        raise ApiError(f"slot must be 0..{SLOTS - 1}")
    return slot


def serialized_action(fn):
    @functools.wraps(fn)
    def wrapped(self, *args, **kwargs):
        with self.action_lock:
            return fn(self, *args, **kwargs)
    return wrapped


# --- application ---------------------------------------------------------------------
class App:
    def __init__(self, demo=False, verbose=False):
        self.demo = demo
        self.device = Device(demo, verbose)
        self.sensors = Sensors(demo)
        self.monitor = None
        self.player = None
        self.config = load_config()
        self.cfg_lock = threading.Lock()
        self.action_lock = threading.RLock()
        self.firmware = None          # read once
        self.storage, self.storage_at = None, 0.0   # the table changes only on upload/delete

    # status ---------------------------------------------------------------------
    def status(self, storage=False):
        """Two commands per poll (display status, current item). The storage table needs a
        three-step handshake that the cooler occasionally leaves unanswered, so it is read
        on demand: after upload/delete, on request (storage=True) and at most once a minute."""
        out = {"demo": self.demo, "version": __version__, "connected": False, "error": self.device.error,
               "monitor": self.monitor_state(), "slideshow": self.player_state(),
               "config": self.config, "slots": SLOTS, "pillow": self._has_pillow()}
        try:
            def q(dev):
                if self.firmware is None:
                    self.firmware = dev.firmware()
                return {"firmware": self.firmware, "path": dev.path,
                        "display": parse_display_status(dev.display_status()), "current": dev.current_item()}
            r = self.device.run(q)
        except ApiError as e:
            out["error"] = str(e)
            out["storage"] = self._annotate_storage(dict(self.storage)) if self.storage else self._cached_storage()
            return out
        kind, typ, slot = r.pop("current")
        r["current"] = {"kind": KIND_NAME.get(kind, f"{kind:02X}"), "type": typ, "slot": slot}
        out.update(r, connected=True)
        if storage or self.storage is None or time.monotonic() - self.storage_at > 60:
            try:
                self.read_storage()
            except ApiError as e:
                out["storage_error"] = str(e)
        out["storage"] = self._annotate_storage(dict(self.storage)) if self.storage else self._cached_storage()
        return out

    def read_storage(self, dev=None):
        st = parse_disk_info(dev.disk_info()) if dev else self.device.run(lambda d: parse_disk_info(d.disk_info()))
        self.storage, self.storage_at = st, time.monotonic()
        return st

    def _cached_storage(self):
        """Offline: what the local copies say."""
        st = {"total_kb": 0, "free_kb": 0}
        for t in ("gif", "jpg"):
            st[t] = {"capacity": SLOTS, "used": [s for s in range(SLOTS) if media_meta(t, s)]}
        return self._annotate_storage(st)

    @staticmethod
    def _annotate_storage(st):
        for t in ("gif", "jpg"):
            items = []
            for s in range(SLOTS):
                meta = media_meta(t, s)
                items.append({"slot": s, "used": s in st[t]["used"], "cached": meta is not None,
                              "name": meta["name"] if meta else None,
                              "bytes": meta["bytes"] if meta else None})
            st[t] = dict(st[t], items=items)
        return st

    @staticmethod
    def _has_pillow():
        try:
            import PIL  # noqa: F401
            return True
        except ImportError:
            return False

    def monitor_state(self):
        m = self.monitor
        if m is None or not m.is_alive():
            return {"running": False}
        return {"running": True, "lines": m.last, "error": m.error, "interval": m.interval}

    def stop_monitor(self):
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor.join(timeout=5)
            if self.monitor.is_alive():
                raise ApiError("live monitor did not stop")
            self.monitor = None

    def player_state(self):
        p = self.player
        if p is None or not p.is_alive():
            return {"running": False}
        return {"running": True, "slots": p.slots, "current": p.current,
                "interval": p.interval, "error": p.error}

    def stop_player(self):
        if self.player is not None:
            self.player.stop()
            self.player.join(timeout=5)
            if self.player.is_alive():
                raise ApiError("slideshow player did not stop")
            self.player = None

    def set_config(self, **upd):
        with self.cfg_lock:
            self.config = merge(self.config, upd)
            save_config(self.config)

    def restore(self):
        """Re-apply the saved configuration where the device cannot keep it by itself: the live
        sensor feed (the panel keeps the last values forever otherwise), the clock (set from the
        host), and a multi-animation slideshow (the rotation is host-driven). A single stored
        animation or wallpaper keeps playing without help."""
        cfg = self.config
        ss = cfg["slideshow"]
        try:
            if cfg["mode"] == "hwmon" and cfg["hwmon"].get("live"):
                self.hwmon(dict(cfg["hwmon"], live=True))
            elif cfg["mode"] == "slideshow" and ss.get("source") == "clock":
                self.show(dict(ss, source="clock"))
            elif cfg["mode"] == "slideshow" and ss.get("source") == "gif" and len(ss.get("gif_slots") or []) > 1:
                self.show({"source": "gif", "slots": ss["gif_slots"], "duration": ss.get("duration", 5)})
            else:
                return None
        except ApiError as e:
            return str(e)
        return cfg["mode"]

    # actions --------------------------------------------------------------------
    @serialized_action
    def display(self, body):
        fields = {}
        if "brightness" in body:
            try:
                b = int(body["brightness"])
            except (TypeError, ValueError):
                raise ApiError("brightness is 0..100")
            if not 0 <= b <= 100:
                raise ApiError("brightness is 0..100")
            fields["brightness"] = b
        if "standby" in body:
            fields["standby"] = 0x10 if body["standby"] else 0x00
        if "anim_slot" in body and body["anim_slot"] is not None:
            fields["anim_type"] = MTYPE["gif"]
            fields["anim_slot"] = check_slot("gif", body["anim_slot"])
        if not fields:
            raise ApiError("nothing to set")
        st = self.device.run(lambda dev: dev.set_display_status(**fields))
        return {"display": parse_display_status(st)}

    @serialized_action
    def hwmon(self, body):
        lines = body.get("lines") or []
        if not 1 <= len(lines) <= 3:
            raise ApiError("1 to 3 lines")
        bg, fg = rgb(body.get("bg", "000000")), rgb(body.get("fg", "FFFFFF"))
        live = bool(body.get("live", False))
        interval = parse_interval(body.get("interval", 2))
        spec, page = [], []
        for l in lines:
            label = str(l.get("label", ""))[:18]
            if l.get("sensor"):
                hw, _, attr = l["sensor"].partition("/")
                if not (hw and attr) or attr.rstrip("0123456789") not in FORMATS:
                    raise ApiError(f"bad sensor {l['sensor']!r}")
                value = self.sensors.read(hw, attr)
                spec.append({"label": label, "sensor": l["sensor"]})
            else:
                value = str(l.get("value", ""))
                spec.append({"label": label, "value": value})
            page.append((label, add_unit_glyphs(value)))
        self.stop_monitor()
        self.stop_player()
        if live and all("sensor" in s for s in spec):
            self.monitor = LiveMonitor(self.device, self.sensors, spec, bg, fg, interval)
            self.monitor.start()
        else:
            def send(dev):
                dev.hwmon(page, 0, None, bg, fg)
                dev.mode(MODE_HWMON)
                settle(dev, MODE_HWMON)
            self.device.run(send)
        self.set_config(mode="hwmon", hwmon={"lines": spec, "count": len(spec), "bg": body.get("bg", "000000"),
                                               "fg": body.get("fg", "FFFFFF"), "live": live, "interval": interval})
        return {"lines": page, "monitor": self.monitor_state()}

    @serialized_action
    def show(self, body):
        source = body.get("source")
        duration = max(1, min(255, int(body.get("duration", 5))))
        slide = {"source": source, "duration": duration}
        if source == "gif":
            slots = body.get("slots")
            if slots is None and body.get("slot") is not None:
                slots = [body["slot"]]
            if not slots:
                raise ApiError("select at least one animation")
            slots = [check_slot("gif", s) for s in slots][:SLOTS]
            slide["gif_slots"] = slots
            self.stop_monitor()
            self.stop_player()
            if len(slots) == 1:
                def go(dev):
                    dev.slideshow_list([("gif", slots[0])], duration)
                    dev.play("gif", slots[0])
                    settle(dev, KIND["gif"])
                self.device.run(go)
            else:
                self.player = SlideshowPlayer(self.device, slots, duration)
                self.player.start()
            self.set_config(mode="slideshow", slideshow=slide)
            return {"ok": True, "slideshow": self.player_state()}
        elif source == "jpg":
            slot = check_slot("jpg", body.get("slot"))
            bn = body.get("banner") or {}
            texts = [str(t)[:48] for t in (bn.get("lines") or [])][:6]
            color = rgb(bn.get("color", "FFFFFFFF"), 4)
            align = 1 if bn.get("align") else 0
            x = max(0, min(319, int(bn.get("x", 8))))
            font = int(bn.get("font", 3))

            def go(dev):
                dev.banner("jpg", slot, texts, font, align, color, duration, x)
                settle(dev, MODE_SLIDESHOW)
            slide["jpg_slot"] = slot
            slide["banner"] = {"lines": (texts + [""] * 6)[:6], "color": bn.get("color", "FFFFFFFF"),
                               "align": align, "x": x, "font": font}
        elif source == "clock":
            h24 = bool(body.get("h24", False))

            def go(dev):
                dev.set_clock(h24=h24)
                dev.slideshow_list([("clock", 1)], duration)
                dev.play("clock", 1)
                settle(dev, KIND["clock"])
            slide["h24"] = h24
        else:
            raise ApiError("source must be gif, jpg or clock")
        self.stop_monitor()
        self.stop_player()
        self.device.run(go)
        self.set_config(mode="slideshow", slideshow=slide)
        return {"ok": True}

    def _prepare_upload(self, query, data):
        ftype = query.get("type", "")
        slot = check_slot(ftype, query.get("slot"))
        name = os.path.basename(query.get("name", f"upload.{EXT.get(ftype, 'bin')}"))[:80]
        if not data:
            raise ApiError("empty upload")
        if query.get("raw") in ("1", "true"):
            out = data
        else:
            crop = None
            if query.get("crop"):
                try:
                    crop = tuple(float(v) for v in query["crop"].split(","))
                    assert len(crop) == 4
                except (ValueError, AssertionError):
                    raise ApiError("crop must be x,y,w,h")
            out = prepare_bytes(data, ftype, crop)
        if not out.startswith(MAGIC[ftype]):
            raise ApiError(f"not a {ftype} file")
        return ftype, slot, name, out

    @serialized_action
    def thumbnail(self, query, data):
        """A local copy for a slot that is used on the device but was filled elsewhere
        (Armoury Crate, the CLI). Converted like an upload; nothing goes to the cooler."""
        ftype, slot, name, out = self._prepare_upload(query, data)
        if self.storage is None:
            try:
                self.read_storage()
            except ApiError:
                pass
        if self.storage and slot not in self.storage[ftype]["used"]:
            raise ApiError(f"{ftype} {slot} is empty on the device; upload into it instead")
        save_media(ftype, slot, out, name)
        return {"type": ftype, "slot": slot, "bytes": len(out), "thumbnail": True}

    @serialized_action
    def upload(self, query, data):
        ftype, slot, name, out = self._prepare_upload(query, data)
        t0 = time.monotonic()

        def go(dev):
            dev.upload(out, ftype, slot)
            self.read_storage(dev)
        self.device.run(go)
        save_media(ftype, slot, out, name)
        return {"type": ftype, "slot": slot, "bytes": len(out), "seconds": round(time.monotonic() - t0, 1)}

    @serialized_action
    def delete(self, ftype, slot):
        slot = check_slot(ftype, slot)
        used = self.device.run(lambda dev: (dev.delete(ftype, slot), self.read_storage(dev))[1][ftype]["used"])
        if slot in used:
            # verified 2026-09-03: the cooler acknowledges the delete but keeps the file
            # while it is on screen; the slot stays in the storage table
            raise ApiError(f"{ftype} {slot} is on screen; show something else first, then delete it", 409)
        forget_media(ftype, slot)
        return {"type": ftype, "slot": slot}

    @serialized_action
    def raw(self, body):
        try:
            payload = bytes.fromhex("".join(str(body.get("hex", "")).replace(",", " ").split()))
        except ValueError:
            raise ApiError("hex bytes expected, e.g. DC or 5C 01")
        if payload and payload[0] == CMD:
            payload = payload[1:]
        if not 1 <= len(payload) <= 64:
            raise ApiError("1 to 64 bytes")
        r = self.device.run(lambda dev: dev.cmd(payload))
        return {"sent": hexs(bytes([CMD]) + payload), "reply": hexs(trim(r))}


# --- HTTP ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ryujin-lcd-web/" + __version__
    app: App = None  # set by serve()
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    auth_token = None

    def log_message(self, fmt, *args):
        if self.app.device.verbose:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # helpers ----------------------------------------------------------------
    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def check_origin(self):
        """Browsers send Origin on cross-site POST/DELETE; refuse those, the API has no login."""
        origin = self.headers.get("Origin")
        if not origin:
            return
        host = urllib.parse.urlsplit(origin).netloc
        if host != self.headers.get("Host", ""):
            raise ApiError(f"cross-origin request from {origin} refused", 403)

    def check_access(self, require_auth=True):
        try:
            host = urllib.parse.urlsplit("//" + self.headers.get("Host", "")).hostname
        except ValueError:
            raise ApiError("invalid Host header", 400)
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            raise ApiError(f"host {host or '(missing)'} is not allowed", 403)
        if require_auth and self.auth_token:
            supplied = self.headers.get("Authorization", "")
            if supplied != f"Bearer {self.auth_token}":
                raise ApiError("authentication required", 401)

    def read_body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ApiError("Transfer-Encoding is not supported", 400)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1:
            raise ApiError("multiple Content-Length headers", 400)
        try:
            n = int(lengths[0]) if lengths else 0
        except ValueError:
            raise ApiError("invalid Content-Length", 400)
        if n < 0:
            raise ApiError("invalid Content-Length", 400)
        if n > MAX_UPLOAD:
            raise ApiError("upload too large (16 MB max)", 413)
        return self.rfile.read(n) if n else b""

    def read_json(self):
        raw = self.read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise ApiError("invalid JSON body")

    def route(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path.rstrip("/") or "/", dict(urllib.parse.parse_qsl(u.query))

    def send_file(self, path, download_name=None):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return self.send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if path.startswith(DATA_DIR) else "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # verbs ------------------------------------------------------------------
    def do_GET(self):
        path, query = self.route()
        try:
            self.check_access(require_auth=path.startswith("/api/"))
            if path == "/api/status":
                return self.send_json(self.app.status(storage=query.get("storage") in ("1", "true")))
            if path == "/api/sensors":
                return self.send_json({"sensors": self.app.sensors.list()})
            if path.startswith("/api/media/"):
                parts = path.split("/")
                if len(parts) == 5:
                    slot = check_slot(parts[3], parts[4])
                    return self.send_file(media_path(parts[3], slot))
            if path.startswith("/api/"):
                return self.send_json({"error": "not found"}, 404)
            # static
            rel = "index.html" if path == "/" else path.lstrip("/")
            full = os.path.normpath(os.path.join(STATIC, rel))
            if not full.startswith(STATIC + os.sep) or not os.path.isfile(full):
                return self.send_json({"error": "not found"}, 404)
            return self.send_file(full)
        except ApiError as e:
            return self.send_json({"error": str(e)}, e.status)

    def do_POST(self):
        path, query = self.route()
        try:
            self.check_access()
            self.check_origin()
            if path == "/api/upload":
                return self.send_json(self.app.upload(query, self.read_body()))
            if path == "/api/thumbnail":
                return self.send_json(self.app.thumbnail(query, self.read_body()))
            body = self.read_json()
            if path == "/api/display":
                return self.send_json(self.app.display(body))
            if path == "/api/hwmon":
                return self.send_json(self.app.hwmon(body))
            if path == "/api/show":
                return self.send_json(self.app.show(body))
            if path == "/api/raw":
                return self.send_json(self.app.raw(body))
            if path == "/api/monitor/stop":
                self.app.stop_monitor()
                self.app.stop_player()
                return self.send_json({"monitor": self.app.monitor_state(),
                                       "slideshow": self.app.player_state()})
            return self.send_json({"error": "not found"}, 404)
        except ApiError as e:
            return self.send_json({"error": str(e)}, e.status)
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            return self.send_json({"error": f"bad request: {e}"}, 400)

    def do_DELETE(self):
        path, _ = self.route()
        try:
            self.check_access()
            self.check_origin()
            parts = path.split("/")
            if path.startswith("/api/media/") and len(parts) == 5:
                return self.send_json(self.app.delete(parts[3], parts[4]))
            return self.send_json({"error": "not found"}, 404)
        except ApiError as e:
            return self.send_json({"error": str(e)}, e.status)


def configure_access(host, token):
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not token:
        raise ApiError("a token is required when binding beyond loopback")
    return ({"127.0.0.1", "localhost", "::1"} if loopback else None), token


def serve(host, port, demo=False, verbose=False, restore=True, token=None):
    Handler.allowed_hosts, Handler.auth_token = configure_access(host, token)
    Handler.app = App(demo, verbose)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"ryujin-lcd-web: http://{host}:{port}/{'  (demo device)' if demo else ''}", flush=True)
    if restore:
        r = Handler.app.restore()
        if r:
            print(f"restored {r} from {CONFIG}" if r in ("hwmon", "slideshow") else f"could not restore the saved mode: {r}", flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # systemctl stop: run the cleanup below
    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        Handler.app.stop_monitor()
        Handler.app.stop_player()
        Handler.app.device.drop()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1", help="bind address (127.0.0.1; 0.0.0.0 for the LAN)")
    ap.add_argument("--port", type=int, default=8686)
    ap.add_argument("--demo", action="store_true", help="simulated device and sensors, no cooler needed")
    ap.add_argument("--no-restore", action="store_true", help="do not re-apply the saved mode at start")
    ap.add_argument("--token", default=os.environ.get("RYUJIN_LCD_TOKEN"),
                    help="Bearer token required for non-loopback API access (or RYUJIN_LCD_TOKEN)")
    ap.add_argument("--import-crate", metavar="MOUNT",
                    help="take thumbnails for the stored media from Armoury Crate's copies on a mounted "
                         "system partition of the OS it ran on (e.g. /mnt), then exit")
    ap.add_argument("-v", "--verbose", action="store_true", help="log requests and every HID report")
    a = ap.parse_args()
    if a.import_crate:
        storage = None
        try:
            dev = Ryujin(a.verbose)
            storage = parse_disk_info(dev.disk_info())
            dev.close()
        except RyujinError as e:
            print(f"cooler not reachable ({e}); importing without checking the slots", file=sys.stderr)
        try:
            rows = import_crate(a.import_crate, storage)
        except ApiError as e:
            sys.exit(str(e))
        for ftype, slot, name, n, note in rows:
            print(f"{ftype} {slot:2d}  {name:45s} {n:7d} B  {note}")
        return
    try:
        serve(a.host, a.port, a.demo, a.verbose, not a.no_restore, a.token)
    except ApiError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
