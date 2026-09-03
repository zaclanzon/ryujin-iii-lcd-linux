#!/usr/bin/env bash
# Install ryujin-lcd for the current user without touching the system Python:
#   udev rule (sudo), package copy to ~/.local/lib/ryujin-lcd, wrappers in ~/.local/bin,
#   and the optional monitor service (./install.sh --monitor).
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
for cmd in cli:ryujin-lcd monitor:ryujin-lcd-monitor; do
  mod=${cmd%%:*}; name=${cmd#*:}
  printf '#!/bin/sh\nPYTHONPATH="%s${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m ryujin_lcd.%s "$@"\n' "$LIB" "$mod" > "$BIN/$name"
  chmod 755 "$BIN/$name"
done

if [ "${1:-}" = "--monitor" ]; then
  echo "==> user service ryujin-lcd-monitor"
  install -Dm644 "$REPO/systemd/ryujin-lcd-monitor.service" "$HOME/.config/systemd/user/ryujin-lcd-monitor.service"
  systemctl --user daemon-reload
  systemctl --user enable --now ryujin-lcd-monitor.service
  systemctl --user try-restart ryujin-lcd-monitor.service
fi
echo "done: ryujin-lcd info"
