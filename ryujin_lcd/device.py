"""ROG Ryujin III (0b05:1aa2) LCD over hidraw + bulk USB. Protocol: docs/protocol.md."""
import datetime
import glob
import io
import os
import select
import struct
import sys
import time

VID, PID = 0x0B05, 0x1AA2
HID_ID = "HID_ID=0003:00000B05:00001AA2"
CMD, EVENT = 0xEC, 0xEE
WIDTH, HEIGHT = 320, 240
CHUNK = 4096

# "get" commands answer with a different command byte; everything else echoes.
REPLY_ID = {0x82: 0x02, 0x99: 0x19, 0x9A: 0x1A, 0xA0: 0x20, 0xA1: 0x21,
            0xD0: 0x50, 0xDC: 0x5C, 0xF1: 0x71}
KIND = {"gif": 0x10, "jpg": 0x04, "clock": 0x08}   # setSlideshowList / playMedia [kind]
MTYPE = {"gif": 0x01, "jpg": 0x00, "clock": 0x00}  # ... [type]
FTYPE = {"gif": 0x02, "jpg": 0x01}                 # SetFileIndex file type
MODE_SLIDESHOW, MODE_HWMON = 0x1F, 0x21
UNIT_GLYPHS = (("°C", "\u2103"), ("C", "\u2103"), ("RPM", "\u218C"), ("V", "\u218A"))


def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


def trim(b):
    return bytes(b).rstrip(b"\0")


class RyujinError(Exception):
    """A command, event or transfer the device did not complete."""


def find_hidraw():
    for uev in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        if HID_ID in open(uev).read().upper():
            return "/dev/" + uev.split("/")[4]
    raise RyujinError("no hidraw node for 0b05:1aa2 (is the cooler connected?)")


class Ryujin:
    def __init__(self, verbose=False):
        self.path = find_hidraw()
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            raise RyujinError(f"{self.path}: permission denied (run as root or install udev/60-ryujin-lcd.rules)")
        self.verbose = verbose
        self.events = []
        self._usb = None

    def log(self, *a):
        if self.verbose:
            print(*a, file=sys.stderr, flush=True)

    # --- HID -----------------------------------------------------------------
    def _read(self, timeout):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return None
        rep = os.read(self.fd, 65)
        if rep and rep[0] == EVENT:
            self.log("  ev", hexs(trim(rep)))
            self.events.append(rep)
        return rep

    def cmd(self, payload, timeout=3.0):
        """Send one 0xEC command (payload = bytes after the report id), return its reply."""
        payload = bytes(payload)
        assert 1 <= len(payload) <= 64, len(payload)
        want = REPLY_ID.get(payload[0], payload[0])
        report = bytes([CMD]) + payload.ljust(64, b"\0")
        self.log("W", hexs(trim(report)))
        os.write(self.fd, report)
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise RyujinError(f"no reply to command {payload[0]:02X} within {timeout}s")
            rep = self._read(left)
            if rep is None or rep[0] != CMD:
                continue
            if rep[1] == want:
                self.log("R", hexs(trim(rep)))
                return rep
            self.log("  skip", hexs(trim(rep)))     # hwmon driver traffic (0x19/0x1A/0x20/0x21)

    def wait_event(self, prefix, timeout=5.0, fatal=True):
        """Wait for an 0xEE event whose bytes [1:] start with prefix."""
        prefix = tuple(prefix)      # ints, or None for "any byte"
        end = time.monotonic() + timeout
        while True:
            for i, ev in enumerate(self.events):
                if all(p is None or ev[1 + j] == p for j, p in enumerate(prefix)):
                    return self.events.pop(i)
            left = end - time.monotonic()
            if left <= 0:
                if fatal:
                    raise RyujinError(f"no event {prefix} within {timeout}s")
                return None
            self._read(left)

    # --- bulk ------------------------------------------------------------------
    def bulk_write(self, data):
        import usb.core
        import usb.util
        if self._usb is None:
            self._usb = usb.core.find(idVendor=VID, idProduct=PID)
            if self._usb is None:
                raise RyujinError("libusb cannot see 0b05:1aa2")
        try:
            n = self._usb.write(0x01, data, timeout=5000)     # claims interface 0 (no kernel driver on it)
        except usb.core.USBError as e:
            raise RyujinError(f"bulk write failed: {e} (need write access to /dev/bus/usb)")
        if n != len(data):
            raise RyujinError(f"bulk write short: {n}/{len(data)}")
        self.log(f"B {n} bytes -> EP 0x01")

    def close(self):
        if self._usb is not None:
            import usb.util
            usb.util.dispose_resources(self._usb)
        os.close(self.fd)

    # --- protocol --------------------------------------------------------------
    def firmware(self):
        return trim(self.cmd(b"\x82")[3:]).decode("ascii", "replace")

    def display_status(self):
        return bytearray(self.cmd(b"\xDC"))

    def set_display_status(self, **fields):
        """Read-modify-write of GetDisplayStatus: brightness, standby, anim_type, anim_slot."""
        st = self.display_status()
        st[2] = 0x01
        idx = {"brightness": 7, "standby": 13, "anim_type": 14, "anim_slot": 15}
        for k, v in fields.items():
            if v is not None:
                st[idx[k]] = v
        if fields.get("brightness") is not None:
            st[12] = fields["brightness"]   # Armoury Crate writes only this byte; the panel follows byte 7
        self.cmd(bytes(st[1:]))
        return self.display_status()

    def disk_info(self):
        self.cmd(b"\x71\x01\x01")           # UpdateDiskInfo
        self.wait_event(b"\x12")
        return self.cmd(b"\xF1")

    def upload(self, data, ftype, slot):
        self.disk_info()
        for attempt in range(2):
            self.cmd(bytes([0x72, 0x01, FTYPE[ftype], slot]))   # SetFileIndex
            self.cmd(b"\x73\x01")                               # begin write
            if self.wait_event(b"\x13\x00\x01", fatal=False):
                break
            # seen once on 2026-09-03: the reply came but not the event, and the slot was
            # left marked used. Closing the operation and starting again worked.
            self.log("  no begin-write ack, closing the operation and retrying")
            self.cmd(b"\x73\xFF")
            time.sleep(1)
        else:
            raise RyujinError("device never acknowledged the file write")
        # captured: 7F 02 <size u16 LE>; the upper bytes were 0 for every captured file, so a
        # u32 LE here is wire-identical for < 64 KiB and the hypothesis for larger files
        r = self.cmd(b"\x7F\x02" + struct.pack("<I", len(data)))
        chunk = struct.unpack("<H", r[3:5])[0] or CHUNK
        for off in range(0, len(data), chunk):
            self.bulk_write(data[off:off + chunk].ljust(chunk, b"\0"))
            self.wait_event(b"\x14")
        self.cmd(b"\x73\xFF")                                   # end write
        self.wait_event(b"\x13\x00\xFF")

    def delete(self, ftype, slot):
        self.cmd(bytes([0x72, 0x01, FTYPE[ftype], slot]))
        self.cmd(b"\x73\x03")
        self.wait_event((0x13, None, 0x03), timeout=3.0)   # seen as 13 10 03 (capture) and 13 00 03

    def slideshow_list(self, entries, duration):
        """entries = [(kind, slot), ...] -> setSlideshowList."""
        body = bytearray([0x5D, 0x00, len(entries)])
        for kind, slot in entries:
            body += bytes([KIND[kind], MTYPE[kind], slot, duration])
        self.cmd(body)

    def play(self, kind, slot):
        self.cmd(bytes([0x51, KIND[kind], MTYPE[kind], slot]))

    def mode(self, m):
        self.cmd(bytes([0x51, m]))

    def set_clock(self, now=None, h24=False):
        now = now or datetime.datetime.now()
        bcd = lambda v: (v // 10 << 4) | v % 10
        hour = now.hour if h24 else (now.hour % 12 or 12)
        # captured for Wed 2026-09-02 23:21:49: EC 11 C6 09 02 03 01 11 21 49 01. Verified 2026-09-03:
        # byte 6 is the 12-hour flag (1 = show AM/PM), the clock page shows only the time; C6 and the
        # month/day/weekday bytes have no visible effect.
        self.cmd(bytes([0x11, 0xC6, now.month, now.day, now.isoweekday() % 7, 0 if h24 else 1,
                        bcd(hour), bcd(now.minute), bcd(now.second), 1 if now.hour >= 12 else 0]))

    def hwmon(self, lines, layout=0, next_layout=None, bg=(0, 0, 0), fg=(255, 255, 255)):
        """Hardware-monitor page: up to 3 (label, value) lines. Unit glyphs live in the
        device font: U+2103 °C, U+218A V, U+218C RPM."""
        if next_layout is None:
            next_layout = len(lines[:3]) - 1    # verified 2026-09-03: commit byte = line count - 1
        begin = bytes([0x52, 0x82, layout, 0x02, 0x02, *bg]) + (bytes([0x00, *fg])) * 3
        self.cmd(begin)
        for i, (label, value) in enumerate(lines[:3]):
            lab = label.encode("ascii", "replace")[:18].ljust(18, b"\0")
            val = value.encode("utf-8")[:42]
            self.cmd(bytes([0x53, i]) + lab + val)
        self.cmd(bytes([0x52, 0x02, next_layout, 0x02, 0x02, *bg]) + b"\xFF" * 17)

    def hwmon_update(self, lines):
        """Replace the values on an already shown hardware-monitor page.

        Only the line commands are sent: the begin/commit pair redraws the page from
        black and flickers, the bare line commands update in place (verified 2026-09-03)."""
        for i, (label, value) in enumerate(lines[:3]):
            lab = label.encode("ascii", "replace")[:18].ljust(18, b"\0")
            val = value.encode("utf-8")[:42]
            self.cmd(bytes([0x53, i]) + lab + val)

    def current_item(self):
        """(kind or mode, source, slot) of what the screen shows; 0x21 = hardware monitor."""
        r = self.cmd(b"\xD0")
        return r[5], r[6], r[7]

    def banner(self, kind, slot, lines, font=3, align=0, color=(255, 255, 255, 255), duration=5, x=8):
        """Wallpaper `slot` with up to 6 text lines (40 px apart), then start the slideshow.
        Verified 2026-09-03: a stored JPG is selected by this background command (0x10 + slot),
        not by the kind-04 list entry, which alone shows a built-in image."""
        self.cmd(bytes([0x60, 0x00, 0x01, 0x10, slot]))
        for i in range(6):
            text = (lines[i] if i < len(lines) else "").encode("utf-8")[:48]
            self.cmd(bytes([0x60, 0x00, 0x02, i, font, align, *color])
                     + struct.pack("<HH", x, 23 + 40 * i) + text)
        self.slideshow_list([(kind, slot)], duration)
        self.mode(MODE_SLIDESHOW)


# --- image preparation ----------------------------------------------------------
def fit(im):
    """Center-crop to 4:3 and resize to 320x240."""
    w, h = im.size
    if w * HEIGHT > h * WIDTH:
        nw = h * WIDTH // HEIGHT
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = w * HEIGHT // WIDTH
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return im.resize((WIDTH, HEIGHT), 1)   # LANCZOS


def flatten(fr):
    """Frame -> opaque RGB. Transparent pixels land on black, the panel's own background,
    and the source's transparency info is dropped: a palette GIF's transparent index turns
    into an RGB tuple on convert(), which Pillow's GIF encoder then refuses to save."""
    from PIL import Image
    if fr.mode == "P" and "transparency" in fr.info or fr.mode in ("RGBA", "LA", "PA"):
        fr = fr.convert("RGBA")
        bg = Image.new("RGB", fr.size, (0, 0, 0))
        bg.paste(fr, mask=fr.getchannel("A"))
        return bg
    fr = fr.convert("RGB")
    fr.info.pop("transparency", None)
    return fr


def encode(im, ftype, prep=None):
    """Re-encode a Pillow image (all frames of a GIF) as a 320x240 JPEG or GIF.
    prep(frame) -> frame runs before the fit; the default is the center 4:3 crop alone."""
    from PIL import Image, ImageSequence
    prep = prep or (lambda fr: fr)
    out = io.BytesIO()
    if ftype == "jpg":
        fit(prep(flatten(im))).save(out, "JPEG", quality=90)
    else:
        frames, durations = [], []
        for fr in ImageSequence.Iterator(im):
            frames.append(fit(prep(flatten(fr))).quantize(256, dither=Image.Dither.NONE))
            durations.append(fr.info.get("duration", 100))
        frames[0].save(out, "GIF", save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, disposal=2)
    return out.getvalue()


def prepare(path, ftype):
    from PIL import Image
    return encode(Image.open(path), ftype)


def add_unit_glyphs(value):
    v = value.strip()
    for suffix, glyph in UNIT_GLYPHS:
        if v.endswith(glyph):
            return v
        if v.endswith(suffix):
            return v[:-len(suffix)].rstrip() + glyph
    return v


def show_info(dev):
    print("firmware     ", dev.firmware())
    sup = dev.cmd(b"\xD0")
    print("checkIsSupport", hexs(trim(sup)))
    st = dev.display_status()
    kind = {0x10: "gif", 0x04: "jpg", 0x08: "clock"}.get(st[8], f"{st[8]:02X}")
    print(f"display       brightness {st[7]}% (byte 12 = {st[12]})  standby {'on' if st[13] else 'off'} "
          f"(anim type {st[14]} slot {st[15]})  current media {kind} type {st[9]} slot {st[10]}")
    print("              raw", hexs(trim(st)))
    d = dev.disk_info()
    total, free = struct.unpack("<II", d[4:12])
    slots = {}
    for name, off in (("other", 12), ("jpg", 17), ("gif", 22)):
        cap, bits = d[off], struct.unpack("<I", d[off + 1:off + 5])[0]
        slots[name] = (cap, [i for i in range(32) if bits >> i & 1])
    print(f"storage       {total} KB total, {free} KB free")
    for name, (cap, used) in slots.items():
        print(f"              {name:5s} {cap} slots, used {used}")
    print("              raw", hexs(trim(d)))
