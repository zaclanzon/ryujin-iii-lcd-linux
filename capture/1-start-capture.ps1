#Requires -RunAsAdministrator
# 1-start-capture.ps1 -- run from an elevated PowerShell (cd C:\Claude\ryujin-capture).
# Finds the USBPcap root hub holding the ROG RYUJIN III (0B05:1AA2), starts a whole-hub capture with a
# large snaplen (bulk media transfers are big), then cold-restarts the ASUS stack so the handshake is captured.
param([string]$Hub = '', [string]$Name = 'ryujin-full')   # -Hub \\.\USBPcapN to force a hub
$ErrorActionPreference = 'Continue'
$dir  = 'C:\Claude\ryujin-capture'
$cmd  = 'C:\Program Files\USBPcap\USBPcapCMD.exe'
$ws   = 'C:\Program Files\Wireshark'
$log  = "$dir\actions-log.txt"
function Note($t) { $s = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $t; Add-Content -Path $log -Value $s -Encoding utf8; Write-Host $s -ForegroundColor Cyan }
if (-not (Test-Path $cmd)) { Write-Host "USBPcapCMD.exe missing - winget install desowin.USBPcap, then reboot." -ForegroundColor Red; exit 1 }
if (-not (Test-Path "$ws\tshark.exe")) { Write-Host "tshark missing - winget install WiresharkFoundation.Wireshark" -ForegroundColor Red; exit 1 }

# 1. USBPcap control devices
$hubs = @(& $cmd --extcap-interfaces 2>&1 | ForEach-Object { if ($_ -match 'value=(\\\\\.\\USBPcap\d+)') { $matches[1] } } | Sort-Object)
if ($hubs.Count -eq 0) { Write-Host "No \\.\USBPcapN devices - the USBPcap filter is not attached. 'sc query USBPcap', reboot if needed." -ForegroundColor Red; exit 1 }
Write-Host "USBPcap control devices: $($hubs -join ', ')"

# 2. Probe every hub for 4 s with descriptor injection; pick the one that shows 0b05:1aa2.
#    (USBPcapCMD's own --extcap-config listing does not reliably match the USBPcapN numbering.)
$listing = @(); $target = $Hub
if (-not $target) {
  $probes = @{}
  foreach ($h in $hubs) { $t = "$env:TEMP\usbpcap-probe-$($h -replace '[^0-9]','').pcap"; $probes[$h] = @{ file=$t; proc=(Start-Process -FilePath $cmd -ArgumentList "-d $h -A --inject-descriptors -o `"$t`"" -PassThru -WindowStyle Hidden) } }
  Start-Sleep 4
  foreach ($h in $hubs) { $pr = $probes[$h]; if (-not $pr.proc.HasExited) { Stop-Process -Id $pr.proc.Id -Force } }
  Start-Sleep 1
  foreach ($h in $hubs) {
    $pr = $probes[$h]
    $devs = & "$ws\tshark.exe" -r $pr.file -Y 'usb.bDescriptorType == 0x01' -T fields -e usb.device_address -e usb.idVendor -e usb.idProduct 2>$null
    $listing += "===== $h ====="; $listing += ($devs | ForEach-Object { '   ' + $_ })
    if (($devs | Out-String) -match '0x0b05\s+0x1aa2') { $target = $h }
  }
  $listing | Set-Content "$dir\hub-listing.txt" -Encoding utf8
  $listing | ForEach-Object { $_ }
}
if (-not $target) { Write-Host "`nCould not find 0B05:1AA2 under any hub. Re-run with -Hub \\.\USBPcapN, or: pnputil /restart-device 'USB\VID_0B05&PID_1AA2\<serial>' while a capture runs." -ForegroundColor Yellow; exit 2 }
Write-Host "`nCapturing on $target" -ForegroundColor Green

# 3. Start the capture in its own console window. -s 262144: keep up to 256 KB of every URB (media uploads
#    go out as WinUSB bulk writes; the default 65535 would truncate them). -b: 128 MB kernel buffer.
$pcap = "$dir\$Name.pcap"
if (Test-Path $pcap) { Move-Item $pcap ("$dir\$Name.{0}.pcap" -f (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$p = Start-Process -FilePath $cmd -ArgumentList "-d $target -A --inject-descriptors -s 262144 -b 134217728 -o `"$pcap`"" -PassThru
Start-Sleep 4
if ($p.HasExited -or -not (Test-Path $pcap)) { Write-Host "USBPcapCMD exited early / no file - see its window." -ForegroundColor Red; exit 3 }
Set-Content "$dir\capture.pid" $p.Id
Set-Content "$dir\capture.name" $Name
Note "CAPTURE STARTED on $target (USBPcapCMD pid $($p.Id)) -> $pcap"

# 4. Cold-restart the ASUS stack inside the capture window (Armoury Crate Service hosts the AIO HAL that
#    talks to the cooler; LightingService is restarted too so its enumeration is in the file).
Note "Stopping ArmouryCrateService + LightingService + ROG Live Service, killing Armoury Crate processes"
foreach ($s in 'ArmouryCrateService','LightingService','ROG Live Service') { Stop-Service $s -Force -ErrorAction SilentlyContinue }
Get-Process | Where-Object { $_.Name -match '^(ArmouryCrate|ArmouryCrate\.Service|ArmouryCrate\.UserSessionHelper|ArmourySocketServer|ArmouryHtmlDebugServer|ArmourySwAgent|asus_framework|LightingService|ROGLiveService)$' } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 5
foreach ($s in 'LightingService','ROG Live Service','ArmouryCrateService') { Note "Starting $s"; Start-Service $s -ErrorAction SilentlyContinue; Start-Sleep 5 }
Note "Launching Armoury Crate UI"
Start-Process explorer.exe 'shell:AppsFolder\B9ECED6F.ArmouryCrate_qmba6cd70vzyy!App'
Start-Sleep 3
Note ("capture file size now {0} bytes" -f (Get-Item $pcap).Length)
Write-Host @"

Capture is running. In Armoury Crate open Device > ROG RYUJIN III (the cooler's own page, NOT Aura Sync).
Before EACH action type in this window:   .\note.cmd <what you are about to do>     (timestamps it)
Wait ~5 s between actions. Full list in README.txt, section "ACTIONS". Do NOT accept a firmware update.
When done:   .\3-stop-capture.ps1
"@ -ForegroundColor Yellow
