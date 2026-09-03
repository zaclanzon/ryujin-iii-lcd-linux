#!/usr/bin/env bash
# Install ryujin-lcd for the current user without touching the system Python:
#   udev rule (sudo), package copy to ~/.local/lib/ryujin-lcd, wrappers in ~/.local/bin,
#   and the optional services (./install.sh --monitor, ./install.sh --web).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HOME/.local/lib/ryujin-lcd"; BIN="$HOME/.local/bin"

python3 -c "import usb.core" 2>/dev/null || echo "pyusb missing: sudo apt install python3-usb  (needed for uploads)"
python3 -c "import PIL" 2>/dev/null || echo "Pillow missing: sudo apt install python3-pil  (needed to resize images; --raw works without it)"

echo "==> udev rule (sudo): hidraw + bulk access for 0b05:1aa2"
sudo install -Dm644 "$REPO/udev/60-ryujin-lcd.rules" /etc/udev/rules.d/60-ryujin-lcd.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=0b05 --attr-match=idProduct=1aa2
sudo udevadm trigger --action=add --subsystem-match=hidraw

echo "==> package -> $LIB, wrappers -> $BIN"
rm -rf "$LIB"; mkdir -p "$LIB" "$BIN"
cp -r "$REPO/ryujin_lcd" "$LIB/"
for cmd in cli:ryujin-lcd monitor:ryujin-lcd-monitor web:ryujin-lcd-web; do
  mod=${cmd%%:*}; name=${cmd#*:}
  printf '#!/bin/sh\nPYTHONPATH="%s${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m ryujin_lcd.%s "$@"\n' "$LIB" "$mod" > "$BIN/$name"
  chmod 755 "$BIN/$name"
done

for arg in "$@"; do
  case "$arg" in
    --monitor) svc=ryujin-lcd-monitor ;;
    --web) svc=ryujin-lcd-web ;;
    *) echo "unknown option $arg (--monitor, --web)"; exit 2 ;;
  esac
  echo "==> user service $svc"
  install -Dm644 "$REPO/systemd/$svc.service" "$HOME/.config/systemd/user/$svc.service"
  systemctl --user daemon-reload
  systemctl --user enable --now "$svc.service"
  systemctl --user try-restart "$svc.service"
done
echo "done: ryujin-lcd info   |   ryujin-lcd-web -> http://127.0.0.1:8686/"
