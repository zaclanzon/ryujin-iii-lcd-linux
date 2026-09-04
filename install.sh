#!/usr/bin/env bash
# Install ryujin-lcd for the current user without touching the system Python, on any distro:
#   udev rule (sudo), a private virtualenv with the Python deps under ~/.local/lib/ryujin-lcd,
#   wrappers in ~/.local/bin, and the optional services (./install.sh --monitor, ./install.sh --web).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HOME/.local/lib/ryujin-lcd"; BIN="$HOME/.local/bin"; VENV="$LIB/venv"

command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
python3 -c "import venv" 2>/dev/null || {
  echo "python3 venv module missing. Install it, e.g.:"
  echo "  Debian/Ubuntu: sudo apt install python3-venv     Fedora/Bazzite: it ships with python3     Arch: it ships with python"
  exit 1
}
# pyusb needs the libusb-1.0 runtime (a system library, not a Python package)
python3 - <<'PY' || echo "note: libusb-1.0 runtime not found; uploads need it (Debian: libusb-1.0-0, Fedora: libusbx, Arch: libusb)"
import ctypes.util, sys
sys.exit(0 if ctypes.util.find_library("usb-1.0") else 1)
PY

echo "==> udev rule (sudo): hidraw + bulk access for 0b05:1aa2 (uaccess grants your local session)"
sudo install -Dm644 "$REPO/udev/60-ryujin-lcd.rules" /etc/udev/rules.d/60-ryujin-lcd.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=0b05 --attr-match=idProduct=1aa2
sudo udevadm trigger --action=add --subsystem-match=hidraw

echo "==> virtualenv + package -> $VENV"
rm -rf "$LIB"; mkdir -p "$LIB" "$BIN"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$REPO[images]"   # pyusb + Pillow + the ryujin-lcd entry points

echo "==> wrappers -> $BIN"
for name in ryujin-lcd ryujin-lcd-monitor ryujin-lcd-web; do
  ln -sf "$VENV/bin/$name" "$BIN/$name"
done
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: $BIN is not on PATH; add it to your shell profile" ;; esac

for arg in "$@"; do
  case "$arg" in
    --monitor) svc=ryujin-lcd-monitor ;;
    --web) svc=ryujin-lcd-web
           if systemctl --user is-enabled -q ryujin-lcd-monitor.service 2>/dev/null; then
             echo "==> disabling ryujin-lcd-monitor (the web panel's live update replaces it)"
             systemctl --user disable --now ryujin-lcd-monitor.service
           fi ;;
    *) echo "unknown option $arg (--monitor, --web)"; exit 2 ;;
  esac
  echo "==> user service $svc"
  install -Dm644 "$REPO/systemd/$svc.service" "$HOME/.config/systemd/user/$svc.service"
  systemctl --user daemon-reload
  systemctl --user enable --now "$svc.service"
  systemctl --user try-restart "$svc.service"
done
echo "done: ryujin-lcd info   |   ryujin-lcd-web -> http://127.0.0.1:8686/"
