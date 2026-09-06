#!/usr/bin/env bash
# Install ryujin-lcd for the current user without touching the system Python, on any distro:
#   udev rule (sudo), a private virtualenv with the Python deps under ~/.local/lib/ryujin-lcd,
#   wrappers in ~/.local/bin, and the optional services (./install.sh --monitor, ./install.sh --web).
#   --no-udev skips the sudo step (re-install after a git pull, or a rule managed elsewhere).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HOME/.local/lib/ryujin-lcd"; BIN="$HOME/.local/bin"; VENV="$LIB/venv"
udev=1; services=(); packages=1; packages_only=0; LINUX_SETUP_DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --no-udev) udev=0 ;;
    --no-packages) packages=0 ;;
    --packages-only) packages_only=1 ;;
    --dry-run) LINUX_SETUP_DRY_RUN=1 ;;
    -h|--help) echo 'Usage: ./install.sh [--monitor|--web] [--no-udev] [--no-packages] [--packages-only] [--dry-run]'; exit 0 ;;
    --monitor|--web) services+=("$arg") ;;
    *) echo "unknown option $arg (--monitor, --web, --no-udev)"; exit 2 ;;
  esac
done

. "$REPO/scripts/linux-deps.sh"
if [[ "$LINUX_SETUP_DRY_RUN" == 0 && "$EUID" == 0 && "$packages_only" == 0 ]]; then
  echo 'Run as your desktop user; system steps use sudo.' >&2; exit 1
fi
[[ "$packages" == 0 ]] || linux_dependencies
[[ "$LINUX_SETUP_DRY_RUN" == 0 ]] || { echo 'Dry run: dependencies, USB rule, venv and requested user services; no changes made.'; exit 0; }
PYTHON=/usr/bin/python3
[[ -x "$PYTHON" ]] || PYTHON=$(command -v python3)
"$PYTHON" "$REPO/scripts/check-linux-deps.py"
[[ "$packages_only" == 0 ]] || exit 0
if (( ${#services[@]} )); then
  systemctl --user show-environment >/dev/null || {
    echo 'Requested service setup needs a running systemd user session.' >&2; exit 1;
  }
fi

if [ "$udev" = 1 ]; then
  echo "==> udev rule (sudo): hidraw + bulk access for 0b05:1aa2 (uaccess grants your local session)"
  sudo install -Dm644 "$REPO/udev/60-ryujin-lcd.rules" /etc/udev/rules.d/60-ryujin-lcd.rules
  sudo udevadm control --reload-rules
  # "change" re-applies the rule (and the uaccess ACL) to the existing nodes; a replayed "add"
  # let other userspace probe the cooler again and once left its HID interface silent (2026-09-04)
  sudo udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=0b05 --attr-match=idProduct=1aa2
  sudo udevadm trigger --action=change --subsystem-match=hidraw
fi

echo "==> virtualenv + package -> $VENV"
mkdir -p "$LIB" "$BIN"
"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$REPO[images]"   # pyusb + Pillow + the ryujin-lcd entry points

echo "==> wrappers -> $BIN"
for name in ryujin-lcd ryujin-lcd-monitor ryujin-lcd-web; do
  ln -sf "$VENV/bin/$name" "$BIN/$name"
done
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: $BIN is not on PATH; add it to your shell profile" ;; esac

for arg in ${services[@]+"${services[@]}"}; do   # (empty-array-safe under set -u on bash < 4.4)
  case "$arg" in
    --monitor) svc=ryujin-lcd-monitor ;;
    --web) svc=ryujin-lcd-web
           if systemctl --user is-enabled -q ryujin-lcd-monitor.service 2>/dev/null; then
             echo "==> disabling ryujin-lcd-monitor (the web panel's live update replaces it)"
             systemctl --user disable --now ryujin-lcd-monitor.service
           fi ;;
  esac
  echo "==> user service $svc"
  install -Dm644 "$REPO/systemd/$svc.service" "$HOME/.config/systemd/user/$svc.service"
  systemctl --user daemon-reload
  systemctl --user enable --now "$svc.service"
  systemctl --user try-restart "$svc.service"
done
echo "done: ryujin-lcd info   |   ryujin-lcd-web -> http://127.0.0.1:8686/"
