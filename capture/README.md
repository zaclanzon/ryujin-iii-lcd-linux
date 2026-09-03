# ROG Ryujin III LCD — capture plan and what is known (2026-09-02)

Goal: the protocol Armoury Crate uses to drive the Ryujin III's 3.5" LCD, so the
screen can be driven from Linux. Nothing public implements it (see *Prior art*).
This folder holds the Windows runbook (`README.txt`), the capture scripts, test
media, and `usbscan.py` for a first look at a USBPcap file on Linux. The same
folder is mirrored to the Windows partition as `C:\Claude\ryujin-capture`.

## Result (2026-09-02 evening)

The capture was run from Windows (two USBPcap runs, 40 min total; the second,
`ryujin-lcd`, covers every action in the runbook). The decoded protocol is in
[docs/protocol.md](../docs/protocol.md) and
implemented by `ryujin-lcd`. Everything brought back from
`C:\Claude\ryujin-capture` except the raw pcaps (1.2 GB, still on the
Windows partition) is in this directory and `analysis/`: `protocol-notes.txt`
(first write-up), `analysis/` (HAL log parsed into a HID timeline, unique
writes/reads, the bulk payloads), `asus-logs/` (the HAL's own log with full
report dumps), the device-only pcapng and tshark transfer dumps,
`actions-log.txt`. Note the HAL log parser trims trailing zeros, so a value
ending in `0` (e.g. the chunk size `10`) prints one digit short.

## Prior art (searched 2026-09-02, nothing found for the LCD)

- liquidctl 1.16.0 (2026-03-03) added Ryujin III 360 / EVA / Extreme / White,
  PID `0x1AA2` included. `AsusRyujin.set_screen` still raises
  `NotSupportedByDriver` with the comment "Not yet reverse engineered / implemented"
  ([driver source](https://github.com/liquidctl/liquidctl/blob/main/liquidctl/driver/asus_ryujin.py)).
  Issue [#869](https://github.com/liquidctl/liquidctl/issues/869) (2026-03-06, Ryujin
  III White) asks for LCD support; no captures, no maintainer reply. The Ryujin II
  PR [#653](https://github.com/liquidctl/liquidctl/pull/653) never covered the LCD either.
- Kernel `Documentation/hwmon/asus_rog_ryujin.rst`: "The addressable LCD screen is not
  supported in this driver and should be controlled through userspace tools."
- [liquidctl/collected-device-data#18](https://github.com/liquidctl/collected-device-data/pull/18)
  has a Ryujin III Extreme `lsusb -v` and a duty-control pcap only. Same interface
  layout as ours.
- OpenRGB issue #690 (LCD feature request) has no Ryujin protocol.
- The only ASUS-cooler LCD project found is
  [asus_ryuo_iv_linux](https://github.com/Jacksongio/asus_ryuo_iv_linux), a different
  device (Android/ADB), not applicable.

## What the device looks like

`lsusb -v` on this machine, device `0b05:1aa2`, bcdDevice 2.00, serial `<serial>`:

| Interface | Class | Endpoints | Linux driver | Windows driver |
|---|---|---|---|---|
| 0 | vendor (0xFF) | bulk `0x01` OUT, `0x81` IN, 512 B | none | WinUSB (MS OS descriptor compat ID) |
| 1 | HID | interrupt `0x82` IN, `0x02` OUT, 64 B | usbhid + `asus_rog_ryujin` (hidraw13 coexists) | HidUsb |

HID report descriptor: usage page `0xFF72`, usage `0xA1`; report `0xEC` 64 B input
and output, report `0xEE` 64 B input. That is the liquidctl/hwmon protocol.
The bulk interface is unused on Linux and is where the media must go.

## What Armoury Crate does (static look at the Windows partition)

- `C:\Program Files\ASUS\Aac_AIOFan\AacAIOFanHal_x64.dll` (v1.5.0.0) imports
  `WinUsb_Initialize/QueryInterfaceSettings/QueryPipe/WritePipe` and `HID.DLL`.
  The Ryujin III is device class **S750** (the White edition's firmware string in
  issue #869 is `AURJ2-S750-0108`). S750 functions: `getDeviceInfo`, `getDeviceStatus`,
  `setDeviceStatus`, `getDiskInfo`, `mediaTransfer`, `deleteMedia`, `addMultiMediaJob`,
  `addMultiHwMonitorJob`, `addTextJob`, `addWarningJob`, `addFanJob`, `setOledDisplayMode`,
  `initLCDEvent/setLCDEvent/resetLCDEvent`, `readChipInfo`/`writeChipInfo`,
  `getPumpSensorData`, `getFanRPM`. Other classes log `sendSegmentDataBulk` and
  `WriteFile2FW`, i.e. media files are written into device storage over bulk.
- `ArmouryAIOSDK.dll` converts uploads before sending: `RotateResizeJPG`,
  `RotateResizeGIF`, `ConvertGIFtoAVI` (`Gif2AviTool.exe`). Expect the wire format to
  be a resized JPG and, for GIFs, possibly an AVI.
- Saved profile `C:\ProgramData\ASUS\Framework\aioFan\RYUJIN3\fp_1_config.xml`
  (base64 of URL-encoded JSON): `display.selectedMode 3`, `brightness 30`,
  `standbymodeStatus`, `warringStatus`, `hardwareMonitor` (themes, sensor slideshow,
  colors), `media` (5 GIFs in `mediaIndex` 0–4, free GIF slots 5–9, JPG slots 0–9),
  `banner` text. So the device stores up to 10 GIF + 10 JPG and plays them itself.
- `deviceinfo.ini`: device page 4.01.31 (server 4.01.39), HAL 1.5.0.0 (server 1.5.10.0),
  AIOFanSDK 3.00.32, plugin 2.00.02; no forced update. A firmware entry exists
  (`Firmware_LocalVersion 0`): decline any firmware update offer.
- The 40-minute whole-hub capture from the Polymo session (`polymo-full.pcap`, bus 4)
  contained the cooler as device 10 with **zero traffic** beyond injected descriptors.
  Only Aura Sync was exercised then; the LCD is driven by the AIO HAL when the cooler's
  page / a display job runs. The runbook opens that page inside the capture.

## Analysis plan once the capture is back

1. `python3 usbscan.py ryujin-full.pcap` — per-device packet counts, endpoints, IDs
   (works on `.pcap` and `.pcapng`, no tshark needed).
2. Split by endpoint: interrupt `0x02/0x82` = HID `0xEC` commands and replies
   (compare with liquidctl's `0x82/0x99/0x9A/0x1A` requests); bulk `0x01/0x81` =
   media transfer and whatever framing wraps it. Line the timestamps up with
   `actions-log.txt` and `asus-logs\<date>.log` (the HAL logs each function call).
3. For the media uploads, diff the bulk payload against `media/*.jpg|gif`: look for
   JPEG `FF D8` / GIF89a / AVI `RIFF` markers, or a raw RGB565 buffer
   (320×320×2 = 204 800 B) to learn the format, then the header before it (slot
   index, length, checksum).
4. Replay from Linux with pyusb on interface 0 (bulk) and hidraw on interface 1;
   the kernel driver only claims the HID interface, so no unbinding is needed.
   Then a `set_screen` for liquidctl's `asus_ryujin.py`.
