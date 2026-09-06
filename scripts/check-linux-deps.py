import ctypes.util
import sys
import venv
if sys.version_info < (3, 9):
    sys.exit("Ryujin LCD requires Python 3.9+.")
if not ctypes.util.find_library("usb-1.0"):
    sys.exit("libusb-1.0 is missing: run ./install.sh to install the distro runtime.")
print(f"Python/venv and libusb OK: {sys.executable}")
