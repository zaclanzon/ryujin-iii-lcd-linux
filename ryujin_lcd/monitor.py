"""Keep the Ryujin III LCD's hardware-monitor page fed from hwmon sensors.

  ryujin-lcd-monitor [--interval S] [--keepalive S] [LINE ...]

Each LINE is LABEL=HWMON/SENSOR, up to three:
  Coolant=rog_ryujin/temp1    Pump=rog_ryujin/fan1    CPU=k10temp/temp1
HWMON is the driver name in /sys/class/hwmon/*/name (looked up on every read, so a
re-enumerated device keeps working), SENSOR the attribute without _input. When several
devices share a name (three NVMe drives are all "nvme"), HWMON can be NAME:DEVICE with
DEVICE the basename of /sys/class/hwmon/hwmonN/device, e.g. nvme:nvme1. Units and
formatting follow the attribute type: temp (°C, 1 decimal), fan (RPM), in (V, 3 decimals),
power (W), curr (A), freq (MHz). Without LINE arguments the file
$XDG_CONFIG_HOME/ryujin-lcd/monitor.conf is read (one LABEL=HWMON/SENSOR per line, # comments),
falling back to the three defaults above.

The full page (layout + mode) is sent once, and again whenever the cooler reports
it is no longer showing the monitor page (checked every --keepalive seconds).
Value changes go out as bare line updates, which the panel redraws in place; the
full page redraws from black and flickers. Nothing is sent while values are
unchanged, so the shared HID interface stays quiet for the hwmon driver.
"""
import argparse
import glob
import os
import sys
import time

from .device import MODE_HWMON, Ryujin, RyujinError, add_unit_glyphs, xdg_config_home

DEFAULT_LINES = ["Coolant=rog_ryujin/temp1", "Pump=rog_ryujin/fan1", "CPU=k10temp/temp1"]
CONFIG = os.path.join(xdg_config_home(), "ryujin-lcd", "monitor.conf")
FORMATS = {   # attribute prefix -> (divisor, format, unit suffix understood by add_unit_glyphs)
    "temp": (1000, "{:.1f}", "°C"),
    "fan": (1, "{:.0f}", " RPM"),
    "in": (1000, "{:.3f}", "V"),
    "power": (1e6, "{:.1f}", "W"),
    "curr": (1000, "{:.2f}", "A"),
    "freq": (1e6, "{:.0f}", "MHz"),
}


def parse_line(spec):
    label, _, sensor = spec.partition("=")
    hw, _, attr = sensor.partition("/")
    if not (label and hw and attr):
        raise SystemExit(f"bad line {spec!r}: want LABEL=HWMON/SENSOR")
    kind = attr.rstrip("0123456789")
    if kind not in FORMATS:
        raise SystemExit(f"{spec}: unknown sensor type {kind!r} (temp/fan/in/power/curr/freq)")
    return label, hw, attr, FORMATS[kind]


def hwmon_id(d):
    """NAME:DEVICE for a hwmon directory, e.g. nvme:nvme1 - tells apart devices that share a
    driver name (several NVMe drives, several DIMMs)."""
    try:
        name = open(os.path.join(d, "name")).read().strip()
    except OSError:
        return None
    dev = os.path.basename(os.path.realpath(os.path.join(d, "device"))) if os.path.exists(os.path.join(d, "device")) else ""
    return f"{name}:{dev}" if dev else name


def hwmon_path(name):
    """Directory of the hwmon device called NAME, or NAME:DEVICE (see hwmon_id)."""
    for n in sorted(glob.glob("/sys/class/hwmon/hwmon*/name")):
        d = os.path.dirname(n)
        try:
            if open(n).read().strip() == name or (":" in name and hwmon_id(d) == name):
                return d
        except OSError:
            pass
    return None


def read_value(hw, attr, fmt, *, path=None):
    # Discovery callers already know the directory. Reuse it for this sample
    # instead of scanning every hwmon device again for each sensor.
    if path is None:
        path = hwmon_path(hw)
    if path is None:
        return "n/a"
    try:
        with open(os.path.join(path, attr + "_input")) as f:
            raw = int(f.read())
    except (OSError, ValueError):
        return "n/a"
    div, pat, unit = fmt
    return pat.format(raw / div) + unit


def load_lines(args):
    if args:
        return args
    try:
        with open(CONFIG) as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except OSError:
        lines = []
    return lines or DEFAULT_LINES


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lines", nargs="*", metavar="LABEL=HWMON/SENSOR")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between sensor reads (2)")
    ap.add_argument("--keepalive", type=float, default=60.0, help="re-send even if unchanged (60 s)")
    ap.add_argument("--once", action="store_true", help="send one page and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    specs = [parse_line(s) for s in load_lines(a.lines)][:3]
    if not specs:
        sys.exit("no lines")
    print("lines:", ", ".join(f"{l}={h}/{s}" for l, h, s, _ in specs), flush=True)

    dev, last, checked_at = None, None, 0.0
    while True:
        page = [(label, read_value(hw, attr, fmt)) for label, hw, attr, fmt in specs]
        now = time.monotonic()
        changed = page != last
        if changed or now - checked_at >= a.keepalive:
            try:
                if dev is None:
                    dev = Ryujin(a.verbose)
                lines = [(l, add_unit_glyphs(v)) for l, v in page]
                if last is None or (now - checked_at >= a.keepalive and dev.current_item()[0] != MODE_HWMON):
                    dev.hwmon(lines)          # full page: layout, lines, commit
                    dev.mode(MODE_HWMON)
                    checked_at = now
                elif changed:
                    dev.hwmon_update(lines)   # lines only: no flicker
                if now - checked_at >= a.keepalive:
                    checked_at = now
                last = page
                if a.verbose or changed:
                    print(time.strftime("%H:%M:%S"), " | ".join(f"{l} {v}" for l, v in page), flush=True)
            except (RyujinError, OSError) as e:
                print(f"{time.strftime('%H:%M:%S')} device error: {e}; retrying in 5 s", file=sys.stderr, flush=True)
                if dev is not None:
                    try:
                        dev.close()
                    except OSError:
                        pass
                dev, last = None, None
                time.sleep(5)
                continue
        if a.once:
            break
        time.sleep(a.interval)
    if dev is not None:
        dev.close()


if __name__ == "__main__":
    main()
