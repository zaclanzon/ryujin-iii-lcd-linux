"""Drive the ROG Ryujin III (0b05:1aa2) 3.5" LCD from Linux.

Protocol: docs/protocol.md (decoded from an Armoury Crate USB capture, capture/). Commands go over the HID interface as 0xEC reports, file
data over the vendor bulk interface. Needs write access to both /dev/hidrawN and
/dev/bus/usb/BBB/DDD (root, or udev/60-ryujin-lcd.rules).

  ryujin-lcd info                              firmware, display status, storage
  ryujin-lcd brightness 10..100
  ryujin-lcd standby on|off [--anim gif SLOT]
  ryujin-lcd upload FILE gif|jpg SLOT [--show] [--raw]   resize to 320x240 and store in slot
  ryujin-lcd show gif|jpg|clock SLOT [--duration S]      play a stored slot
  ryujin-lcd delete gif|jpg SLOT
  ryujin-lcd hwmon "CPU=43.0°C" "Pump=1680 RPM" "Volt=1.066V"   up to 3 lines
  ryujin-lcd banner jpg SLOT "line 0" ["line 1" ...]     wallpaper + up to 6 text lines
  ryujin-lcd clock                             set the device clock and show it
  ryujin-lcd raw 5C 01 ...                     send one command, print the reply
  ryujin-lcd monitor [SECONDS]                 print every incoming report
"""
import argparse
import sys
import time

from .device import Ryujin, RyujinError, CMD, MTYPE, MODE_HWMON, MODE_SLIDESHOW, hexs, trim, prepare, add_unit_glyphs, show_info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="print every report")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    p = sub.add_parser("brightness"); p.add_argument("percent", type=int)
    p = sub.add_parser("standby"); p.add_argument("state", choices=["on", "off"])
    p.add_argument("--anim", nargs=2, metavar=("gif", "SLOT"))
    p = sub.add_parser("upload"); p.add_argument("file"); p.add_argument("type", choices=["gif", "jpg"])
    p.add_argument("slot", type=int); p.add_argument("--show", action="store_true")
    p.add_argument("--raw", action="store_true", help="send the file as-is (must already be 320x240)")
    p = sub.add_parser("show"); p.add_argument("type", choices=["gif", "jpg", "clock"]); p.add_argument("slot", type=int)
    p.add_argument("--duration", type=int, default=5)
    p.add_argument("--slideshow", action="store_true", help="start via display mode 1F instead of playMedia")
    p = sub.add_parser("delete"); p.add_argument("type", choices=["gif", "jpg"]); p.add_argument("slot", type=int)
    p = sub.add_parser("hwmon"); p.add_argument("lines", nargs="+", metavar="LABEL=VALUE")
    p.add_argument("--layout", type=int, default=0, help="begin byte (no visible effect seen)")
    p.add_argument("--next", type=int, help="commit byte, default line count - 1")
    p.add_argument("--bg", default="000000"); p.add_argument("--fg", default="FFFFFF")
    p.add_argument("--no-glyphs", action="store_true")
    p = sub.add_parser("banner"); p.add_argument("type", choices=["jpg"]); p.add_argument("slot", type=int)
    p.add_argument("lines", nargs="*"); p.add_argument("--font", type=int, default=3)
    p.add_argument("--align", type=int, default=0, help="0 left, 1 right (anchored at --x)")
    p.add_argument("--x", type=int, default=8); p.add_argument("--color", default="FFFFFFFF")
    p = sub.add_parser("clock"); p.add_argument("--24h", dest="h24", action="store_true")
    p = sub.add_parser("raw"); p.add_argument("bytes", nargs="+")
    p = sub.add_parser("monitor"); p.add_argument("seconds", type=float, nargs="?", default=10)
    a = ap.parse_args()

    try:
        dev = Ryujin(a.verbose)
    except RyujinError as e:
        sys.exit(str(e))
    try:
        if a.cmd == "info":
            show_info(dev)
        elif a.cmd == "brightness":
            if not 0 <= a.percent <= 100:
                raise RyujinError("brightness is 0..100 (Armoury Crate uses 10..100 in steps of 10)")
            st = dev.set_display_status(brightness=a.percent)
            print(f"brightness now {st[7]}% (byte 12 = {st[12]})")
        elif a.cmd == "standby":
            f = {"standby": 0x10 if a.state == "on" else 0x00}
            if a.anim:
                f.update(anim_type=MTYPE[a.anim[0]], anim_slot=int(a.anim[1]))
            st = dev.set_display_status(**f)
            print(f"standby {'on' if st[13] else 'off'}, animation type {st[14]} slot {st[15]}")
        elif a.cmd == "upload":
            data = open(a.file, "rb").read() if a.raw else prepare(a.file, a.type)
            magic = {"gif": b"GIF8", "jpg": b"\xFF\xD8"}[a.type]
            if not data.startswith(magic):
                raise RyujinError(f"{a.file}: not a {a.type} after conversion")
            t0 = time.monotonic()
            dev.upload(data, a.type, a.slot)
            print(f"stored {len(data)} bytes in {a.type} slot {a.slot} ({time.monotonic() - t0:.1f}s)")
            if a.show and a.type == "jpg":
                dev.banner("jpg", a.slot, [])
            elif a.show:
                dev.slideshow_list([(a.type, a.slot)], 5)
                dev.play(a.type, a.slot)
        elif a.cmd == "show":
            if a.type == "clock":
                dev.set_clock()
            if a.type == "jpg":
                dev.banner("jpg", a.slot, [], duration=a.duration)
                return
            dev.slideshow_list([(a.type, a.slot)], a.duration)
            if a.slideshow:
                dev.mode(MODE_SLIDESHOW)
            else:
                dev.play(a.type, a.slot)
        elif a.cmd == "delete":
            dev.delete(a.type, a.slot)
            print(f"deleted {a.type} slot {a.slot}")
        elif a.cmd == "hwmon":
            lines = []
            for s in a.lines:
                label, _, value = s.partition("=")
                lines.append((label, value if a.no_glyphs else add_unit_glyphs(value)))
            rgb = lambda h: tuple(bytes.fromhex(h))
            dev.hwmon(lines, a.layout, a.next, rgb(a.bg), rgb(a.fg))
            dev.mode(MODE_HWMON)
        elif a.cmd == "banner":
            dev.banner(a.type, a.slot, a.lines, a.font, a.align, tuple(bytes.fromhex(a.color)), x=a.x)
        elif a.cmd == "clock":
            dev.set_clock(h24=a.h24)
            dev.slideshow_list([("clock", 1)], 5)
            dev.play("clock", 1)
        elif a.cmd == "raw":
            payload = bytes.fromhex("".join(a.bytes))
            if payload and payload[0] == CMD:
                payload = payload[1:]
            r = dev.cmd(payload)
            print(hexs(trim(r)))
        elif a.cmd == "monitor":
            end = time.monotonic() + a.seconds
            while time.monotonic() < end:
                rep = dev._read(0.5)
                if rep:
                    print(f"{time.strftime('%H:%M:%S')} {hexs(trim(rep))}", flush=True)
    except RyujinError as e:
        sys.exit(str(e))
    finally:
        dev.close()


if __name__ == "__main__":
    main()
