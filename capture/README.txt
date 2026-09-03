ROG RYUJIN III (0B05:1AA2) LCD - USB CAPTURE RUNBOOK (Windows side)
====================================================================
Folder: C:\Claude\ryujin-capture      (Linux copy: capture/ in the ryujin3-lcd repo)

GOAL
  Record what Armoury Crate sends to the cooler when it drives the 3.5" LCD, so the protocol can be
  implemented on Linux (liquidctl's set_screen for this device is still "Not yet reverse engineered").
  Only the LCD matters. Do not change fan or pump settings, do not accept a firmware update.

WHAT IS ALREADY KNOWN (from the Linux side, 2026-09-02)
  - The cooler is one USB composite device 0B05:1AA2, serial <serial>, two interfaces:
      MI_00  vendor-specific, bulk EP 0x01 OUT / 0x81 IN (512 B) -> bound to WinUSB (MS OS descriptor)
      MI_01  HID, interrupt EP 0x82 IN / 0x02 OUT (64 B), reports 0xEC (64 B in/out) and 0xEE (64 B in)
    Armoury Crate's AIO HAL (C:\Program Files\ASUS\Aac_AIOFan\AacAIOFanHal_x64.dll, device class
    "S750" = Ryujin III) imports WinUsb_WritePipe/QueryPipe next to HID.DLL: media goes over the bulk
    pipe, control/status over HID. Function names in the DLL: getDeviceInfo, getDiskInfo, mediaTransfer,
    deleteMedia, addMultiMediaJob, addMultiHwMonitorJob, addTextJob, addWarningJob, setDeviceStatus,
    setOledDisplayMode, setLCDEvent, readChipInfo/writeChipInfo. The saved profile
    (C:\ProgramData\ASUS\Framework\aioFan\RYUJIN3\fp_1_config.xml, base64 of URL-encoded JSON) shows
    10 GIF slots + 10 JPG slots on the device, brightness 30, "hardwareMonitor" themes, "media" slideshow,
    "banner" text, standby and warning modes.
  - In the Polymo capture of 2026-09-01 (same root hub, 40 min) the cooler received ZERO packets, so the
    LCD is only driven while the cooler's device page / a display job is active. Open the page.
  - All Armoury Crate components for the cooler are installed (device page 4.01.31, AacAIOFanSetup 1.5.0.0,
    AIOFanSDK 3.00.32, AIOFanSDKPlugin 2.00.02); a newer server version exists but no update is forced.
    If the page asks to update a *component*, that is fine. If it offers a *firmware* update: DECLINE.

TOOLS (should still be installed from the Polymo capture; check first)
  C:\Program Files\Wireshark\tshark.exe     (winget install WiresharkFoundation.Wireshark)
  C:\Program Files\USBPcap\USBPcapCMD.exe   (winget install desowin.USBPcap ; needs a reboot to attach)
  Check the filter is live:   sc query USBPcap     and    USBPcapCMD.exe --extcap-interfaces

STEPS  (elevated PowerShell:  cd C:\Claude\ryujin-capture ; Set-ExecutionPolicy -Scope Process Bypass)
  0.  .\0-device-notes.ps1        -> device-manager-notes.txt (drivers per interface, WinUSB GUID, services)
  1.  .\1-start-capture.ps1       -> probes every USBPcap hub, captures the one with 0B05:1AA2 (whole hub,
                                     snaplen 256 KB), restarts LightingService / ROG Live Service /
                                     ArmouryCrateService, launches Armoury Crate. Handshake is in the file.
        If it cannot find the device:  .\1-start-capture.ps1 -Hub \\.\USBPcapN   (see hub-listing.txt), or
        force a re-enumeration while capturing:  pnputil /restart-device "USB\VID_0B05&PID_1AA2\<serial>"
  2.  ACTIONS (below). Before each one:   .\note.cmd <text>    Wait ~5 s between actions.
  3.  .\3-stop-capture.ps1        -> ryujin-full.pcapng, ryujin-full-ryujin-device-only.pcapng,
                                     ryujin-full-transfers.txt (one line per control/interrupt/bulk transfer),
                                     ryujin-full-transfers-verbose.txt, ryujin-full-stats.txt, asus-logs\
  A second run (e.g. only media uploads) can use  .\1-start-capture.ps1 -Name ryujin-media  and
  .\3-stop-capture.ps1 -Name ryujin-media.

ACTIONS  (Armoury Crate > Device > ROG RYUJIN III; note the tab names as you see them)
  a. Open the cooler's page. Note what the LCD shows before you touch anything.
  b. Display / Hardware monitor: select it. Then change the theme once, change which sensors are shown,
     change the duration (5 s -> 10 s). Leave it running ~30 s so the periodic sensor updates are captured.
  c. Brightness: 30 -> 100, wait, -> 50, wait, -> 30.
  d. Orientation / rotation (if the page has it): rotate once, rotate back.
  e. Custom image: upload media\red.jpg from this folder, select it, wait 10 s.
     Then upload media\stripes.jpg (larger, has structure), select it.
     Then upload media\rgbw-4frames.gif (4 solid frames red/green/blue/white, 0.5 s each), select it, wait 10 s.
     Solid colors make the on-wire image format obvious (raw RGB565/RGB888 vs JPEG vs converted AVI).
  f. Delete one of the uploaded items (deleteMedia).
  g. Text / banner (if present): set the text "ABC", apply, then clear it.
  h. Standby / display off (if present): off, wait, on.
  i. Warning mode: toggle the temperature-warning switch off and on.
  j. Finally put the page back to Hardware monitor as it was (brightness 30) and note it.
  k. Optional, separately: in Aura Sync change the cooler's RGB once (to tell LED traffic from LCD traffic).

BRING BACK (everything in C:\Claude\ryujin-capture; the Linux side reads the NTFS partition directly)
  ryujin-full.pcap/.pcapng, *-ryujin-device-only.pcapng, *-transfers.txt, *-transfers-verbose.txt,
  *-stats.txt, actions-log.txt, hub-listing.txt, device-manager-notes.txt, asus-logs\

WIRESHARK CHEAT SHEET
  usb.idVendor == 0x0b05 && usb.idProduct == 0x1aa2     (descriptor packets only; gives the address)
  usb.device_address == N                                (everything for the cooler)
  usb.transfer_type == 0x03                              (bulk = WinUSB pipe on MI_00: media/LCD payloads)
  usb.transfer_type == 0x01                              (interrupt = HID reports 0xEC/0xEE on MI_01)
  usb.transfer_type == 0x02 && usb.bmRequestType == 0x21 (HID SET_REPORT, wValue 0x02EC/0x03EC, wIndex 1)

UNDO: nothing on the system is changed permanently by these scripts. Armoury Crate settings you changed
      in step j are restored by hand.
