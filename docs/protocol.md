# ROG Ryujin III (0b05:1aa2) LCD protocol

Decoded on 2026-09-02 from a USBPcap capture of Armoury Crate (AacAIOFanHal
1.5.10.0, device class `S750`, firmware `AURJ2-S750-0108`) driving the
cooler's 3.5" 320×240 screen. Source material and the HAL's own per-command
log are in [capture/](../capture/)
(`protocol-notes.txt` is the first-pass write-up, `analysis/hid-timeline.txt`
every HID report with a timestamp). `ryujin-lcd` implements this.

Status of each section: **captured** = seen on the wire and reproduced by the
HAL log; **verified** = replayed from Linux on 2026-09-03 with the result
checked on the panel. Everything the tool does is verified unless marked
otherwise.

## Transport

Composite USB device, two interfaces:

| Interface | Class | Endpoints | Linux driver | Used for |
|---|---|---|---|---|
| 0 | vendor 0xFF | bulk OUT `0x01`, IN `0x81`, 512 B | none | file data only (4096-byte writes) |
| 1 | HID | interrupt OUT `0x02`, IN `0x82`, 64 B | usbhid + `asus_rog_ryujin` | every command and reply |

- A command is one 65-byte output report: report id `0xEC`, then 64 bytes,
  unused bytes zero. Written to the hidraw node as-is (interrupt OUT; no
  SET_REPORT is used).
- Every command gets one 64-byte input report `EC <cmd> ...`. The "get"
  commands answer with a different id: `82→02`, `99→19`, `9A→1A`, `A0→20`,
  `A1→21`, `D0→50`, `DC→5C`, `F1→71`. Everything else echoes the command byte.
  Typical latency 30 ms, 120–230 ms for layout/status commands, ~500 ms for
  file operations.
- Unsolicited events use report id `0xEE` and only appear during file
  operations (below).
- The hwmon driver on the same interface polls `EC 99`/`9A`/`A0`/`A1`; its
  replies (`19/1A/20/21`) show up on hidraw too and must be skipped. The
  driver ignores any reply it does not recognize, so the LCD traffic does not
  disturb it. Never write pump/fan duty from the LCD tool (see the README's
  pump-duty note).

## Command map (byte 0 = `EC`, byte 1 = command)

| cmd | HAL name | meaning |
|---|---|---|
| `11` | setClock | set the device clock (clock slideshow item) |
| `51` | playMedia / setOledDisplayMode | show a stored item, or select a mode (`1F` slideshow, `21` hardware monitor) |
| `52` | setHWmonitorLayout | hardware-monitor page: begin (`82`) / commit (`02`) |
| `53` | setHWmonitorString | one hardware-monitor line: label + value |
| `5C` | SetDisplayStatus | brightness, standby, standby animation (also the reply id of `DC`) |
| `5D` | setSlideshowList | list of items to cycle |
| `60` | setSlideshowBannerBG / Text | wallpaper background + up to 6 text lines |
| `71` | UpdateDiskInfo | `EC 71 01 01`: refresh the storage table (also the reply id of `F1`) |
| `72` | SetFileIndex | select a media slot: `EC 72 01 <ftype> <slot>` |
| `73` | SetFileOperation | `01` begin write, `FF` end write, `03` delete selected slot |
| `7F` | enableFileStreamTransfer | announce the file size; reply carries the chunk size |
| `82` | GetFwVersion | reply `EC 02 00 "AURJ2-S750-0108"` |
| `99` | GetPumpSensorData | hwmon driver territory; reply `EC 19 ...` |
| `D0` | checkIsSupport | reply `EC 50 00 01 30 10 01 04 01` |
| `DC` | GetDisplayStatus | reply `EC 5C ...` (layout below) |
| `F1` | GetDiskInfo | reply `EC 71 ...` (layout below) |

Media addressing used by `51`, `5D`, `60` (kind, type, slot):

| item | kind | type | slot |
|---|---|---|---|
| custom GIF | `10` | `01` | 0..9 (device has 16 bits in the bitmap, Armoury Crate uses 10) |
| custom JPG | `04` | `00` | 0..9 |
| clock | `08` | `00` | `01` |

File type for `SetFileIndex` is different: `02` = GIF, `01` = JPG.

## Display status (`EC DC` → `EC 5C ...`; write back with byte 2 = `01`)

```
idx:  2  3  4  5  6  7  8  9  10 11 12 13 14 15
      00 00 64 00 00 64 10 01 04 00 1E 10 01 04
      |     |        |  |  |  |     |  |  |  +- standby animation slot
      |     |        |  |  |  |     |  |  +---- standby animation type (01 gif)
      |     |        |  |  |  |     |  +------- standby enable: 10 on, 00 off
      |     |        |  |  |  |     +---------- brightness value Armoury Crate writes (no visible effect)
      |     |        |  |  |  +---------------- current media slot
      |     |        |  |  +------------------- current media type
      |     |        +---------------------------- current media kind
      |     |        +- PANEL BRIGHTNESS (byte 7): the only byte that changes the backlight
      |     +---------- 100, no visible effect when changed
      +---------------- 00 in the reply, 01 when setting
```

**Verified:** Armoury Crate only ever wrote byte 12, and on Linux changing
byte 12 (or byte 4) did nothing at all, even after restarting the media.
Byte 7 changes the backlight immediately. The tool writes both 7 and 12.
The HAL always does Get, Set (same bytes with byte 2 = `01`), Get. Turning
standby off also wrote byte 12 = 0; turning it on wrote `00 10` then `1E 10`. Temperature warning is host-side only
(the HAL polls the CPU and draws a warning page itself); nothing reaches the
device until it trips.

## Hardware monitor page (`52`, `53`, `51 21`)

```
EC 52 82 <layout> 02 02 <bg R G B> 00 <R G B> 00 <R G B> 00 <R G B>   begin, per-line text colors
EC 53 <line> <label: 18 bytes ASCII, NUL padded> <value: UTF-8, NUL terminated>
EC 52 02 <layout'> 02 02 <bg R G B> FF ×17                            commit
EC 51 21                                                              mode = hardware monitor
```

- Up to 3 lines. Unit glyphs are private code points in the device font:
  U+2103 (`E2 84 83`) °C, U+218A (`E2 86 8A`) V, U+218C (`E2 86 8C`) RPM.
- `<layout'>` in the commit is **the line count minus one** (verified with
  1, 2 and 3 lines; a wrong value, e.g. `00` with three lines, shows nothing).
  The begin byte (`00` or `02` in the capture) made no visible difference.
- `02 02` at bytes 4–5 was constant (theme 0, horizontal). Only black
  background / white text was captured; the color bytes are placed where the
  captured `00 00 00` and `FF FF FF` were. Colors other than those are
  untested. Unit glyphs render correctly.
- The HAL re-sends the group every ~5.5 s for as long as the job exists; the
  device keeps showing the last strings, so a Linux daemon only needs to send
  when values change. **Send only the `53` line commands for an update**: the
  begin/commit pair redraws the page from black and flickers visibly, while
  bare line commands replace the values in place (verified 2026-09-03).

## Slideshow and media playback (`5D`, `51`, `60`, `11`)

```
EC 5D 00 <n> { <kind> <type> <slot> <seconds> } ×n     setSlideshowList (only n = 1 captured)
EC 51 <kind> <type> <slot>                            playMedia: show this now
EC 51 1F                                              setOledDisplayMode: play the list (HAL uses it for JPG/banner)
```

The second byte of the triple is the SDK's `source` (0 = uploaded file,
1 = built into the firmware): the clock entry is profile type 3 / source 1 /
index 1 and goes out as `08 00 01`.

GIF: `EC 5D 00 01 10 01 <slot> 05` then `EC 51 10 01 <slot>` plays the stored
GIF, including a 400 KB, 30-frame one. **Verified.**

JPG: the kind-`04` list entry does **not** select a stored JPG. `EC 51 04 00 09`
and the HAL's own `5D 00 01 04 00 01 05` + `51 1F` both showed a built-in ROG
image (and the current-item readback reports slot 0). A stored JPG is
selected by the wallpaper background command:

```
EC 60 00 01 10 <jpg slot>                              background = stored JPG (10 = uploaded)
EC 60 00 02 <line 0..5> <font> <align> <R G B A> <x u16 LE> <y u16 LE> <text UTF-8>   ×6, empty to clear
EC 51 1F                                              slideshow mode
```

`60 00 01 10 09` followed by `51 1F` alone was enough. The list command is
optional. In the capture the HAL sent `10 01` for stripes (jpg 1) and `10 00`
for red (jpg 0); the parser had trimmed the trailing zero. **Verified.**

Text line fields, all verified on the panel: `font` selects the typeface
(3 and 1 look the same size), `align` is 0 = left, anything else = right,
both anchored at `x`; `R G B A` with A as opacity (`40` is faint); y
positions 23, 63, 103, 143, 183, 223 (40 px apart). There is no center
alignment and no size byte; compute x on the host.

Clock item: `EC 11 C6 09 02 03 01 11 21 49 01` for Wed 2026-09-02 23:21:49,
then `EC 5D 00 01 08 00 01 05` and `EC 51 08 00 01`. Byte 6 is the 12-hour
flag: `01` shows AM/PM, `00` hides it (send the hour in 24-hour BCD then).
The page shows only the time, so `C6`, month, day and weekday have no
visible effect. **Verified.**

## Media upload (the part liquidctl's `set_screen` needs)

Armoury Crate crops and resizes on the host (320×240; a GIF stays a GIF, a
JPG stays a JPEG) and the device stores the bytes verbatim. Upload of a
2070-byte GIF into GIF slot 5:

```
EC 71 01 01                 UpdateDiskInfo        → EC 71, then event EE 12 00 00
EC F1                       GetDiskInfo           → EC 71 00 01 <total u32 LE, KB> <free u32 LE, KB> ...
EC 72 01 02 05              SetFileIndex: type 02 gif (01 jpg), slot 5
EC 73 01                    begin write           → EC 73, then event EE 13 00 01
EC 7F 02 16 08              size 0x0816 = 2070    → EC 7F 00 00 10 : chunk size 0x1000 = 4096
bulk OUT 0x01: 4096 bytes   file bytes, last chunk zero-padded to 4096
                            → event EE 14 00 00 xx after EACH chunk (host waits for it)
EC 73 FF                    end write             → EC 73, then event EE 13 00 FF
```

A 5785-byte JPG went as two 4096-byte bulk writes with an `EE 14` between
them. Delete: `EC 72 01 01 00` (jpg slot 0) then `EC 73 03`, ack event
`EE 13 10 03` in the capture, `EE 13 00 03` on Linux; the slot leaves the
bitmap immediately. **Verified.**

Size field: the capture only had files < 64 KiB (`7F 02 <u16 LE>`). A
400 059-byte GIF sent as `7F 02 BB 1A 06` (u32 LE at bytes 3–6) was accepted
in 98 chunks and then played from its slot. **Confirmed 2026-09-03.**

Once, the reply to `73 01` came without the `EE 13 00 01` event and the slot
was left marked used in the storage table; `73 FF` then a fresh
`72`/`73 01` worked. The tool retries once that way.

GetDiskInfo reply (bytes after `EC 71 00 01`):

```
[4..7]   total KB u32 LE   (0x7EA8 = 32424)
[8..11]  free KB u32 LE    (21880; each stored file cost 8 KB)
[12]     10  [13..16] bitmap u32 LE   third media class, unused (16 slots)
[17]     10  [18..21] bitmap u32 LE   JPG slots (01 once red.jpg was in slot 0)
[22]     10  [23..26] bitmap u32 LE   GIF slots (1F = slots 0–4, 3F after slot 5)
```

The 16/bitmap grouping is confirmed: with slot 9 added the words read
`03 02 00 00` (jpg 0, 1, 9) and `3F 02 00 00` (gif 0–5, 9).

`checkIsSupport` (`EC D0` → `EC 50 00 01 30 <kind> <type> <slot> 01`) reports
the item currently on screen: `21` for the hardware monitor, `1F` after mode
`1F`, `08 00 00` for the clock, `10 01 09` for GIF slot 9. It lags until the
device has loaded the file, so read it a second or two after a play command.

## Still unknown

Standby mode was only written, never observed. Hardware-monitor colors and
the `02 02` theme bytes, the `C6` clock byte, the third media class in the
storage table, and what `60 00 01 00 xx` (background without the `10`
flag) selects. Portrait orientation was never exercised.

## Access

Run as root, or install `config/60-ryujin-lcd.rules` (install.sh does). The
LCD state survives the host: whatever was last selected keeps playing after
the tool exits, and after a reboot.
