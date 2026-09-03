#Requires -RunAsAdministrator
# 3-stop-capture.ps1 -- stops USBPcapCMD, converts to pcapng, exports the 0B05:1AA2-only pcapng, writes a
# one-line-per-transfer dump (control, interrupt AND bulk), and collects the ASUS diagnosis logs for the day.
param([string]$Name = '')
$dir = 'C:\Claude\ryujin-capture'; $ws = 'C:\Program Files\Wireshark'
if (-not $Name) { $Name = (Get-Content "$dir\capture.name" -ErrorAction SilentlyContinue); if (-not $Name) { $Name = 'ryujin-full' } }
$log = "$dir\actions-log.txt"
function Note($t) { $s = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $t; Add-Content $log $s -Encoding utf8; Write-Host $s -ForegroundColor Cyan }
$savedPid = Get-Content "$dir\capture.pid" -ErrorAction SilentlyContinue
$proc = $null
if ($savedPid) { $proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue }
if (-not $proc) { $proc = Get-Process USBPcapCMD -ErrorAction SilentlyContinue }
if ($proc) { $proc | Stop-Process -Force; Start-Sleep 2; Note "CAPTURE STOPPED ($Name)" } else { Note "CAPTURE STOPPED ($Name) (USBPcapCMD was already gone)" }

$pcap = "$dir\$Name.pcap"; $full = "$dir\$Name.pcapng"; $dev = "$dir\$Name-ryujin-device-only.pcapng"
& "$ws\editcap.exe" -F pcapng $pcap $full
"full capture : $full  ($((Get-Item $full).Length) bytes)"
$addrs = & "$ws\tshark.exe" -r $full -Y 'usb.idVendor == 0x0b05 && usb.idProduct == 0x1aa2' -T fields -e usb.bus_id -e usb.device_address 2>$null | Sort-Object -Unique
if (-not $addrs) {
  Write-Host "No device descriptor for 0B05:1AA2 in the capture: wrong hub (see hub-listing.txt) or nothing enumerated. See README.txt." -ForegroundColor Red
} else {
  $filt = ($addrs | ForEach-Object { $b,$a = $_ -split "`t"; "(usb.bus_id == $b && usb.device_address == $a)" }) -join ' || '
  "device address filter: $filt" | Tee-Object -FilePath "$dir\$Name-device-filter.txt"
  & "$ws\tshark.exe" -r $full -Y $filt -w $dev
  "device-only   : $dev  ($((Get-Item $dev).Length) bytes)"
  # one line per transfer: control (0x02), interrupt (0x01) and bulk (0x03); bulk payload hex is in usb.capdata
  $fields = @('frame.number','frame.time','usb.src','usb.dst','usb.transfer_type','usb.endpoint_address','usb.data_len','usb.bmRequestType','usb.setup.bRequest','usb.setup.wValue','usb.setup.wIndex','usb.setup.wLength','usb.data_fragment','usbhid.data','usb.capdata')
  $targs = @('-r',$dev,'-Y','usb.transfer_type == 0x02 || usb.transfer_type == 0x01 || usb.transfer_type == 0x03','-T','fields','-E','header=y','-E','separator=|')
  foreach ($f in $fields) { $targs += '-e'; $targs += $f }
  & "$ws\tshark.exe" @targs | Set-Content "$dir\$Name-transfers.txt" -Encoding utf8
  "transfer dump : $dir\$Name-transfers.txt"
  # full dissection of everything except bulk data packets (those can be 256 KB each; use the pcapng for them)
  & "$ws\tshark.exe" -r $dev -V -Y '(usb.transfer_type == 0x02 || usb.transfer_type == 0x01) || (usb.transfer_type == 0x03 && usb.data_len < 512)' | Set-Content "$dir\$Name-transfers-verbose.txt" -Encoding utf8
  "verbose dump  : $dir\$Name-transfers-verbose.txt"
  & "$ws\tshark.exe" -r $dev -q -z 'io,stat,0,usb.transfer_type==0x01,usb.transfer_type==0x02,usb.transfer_type==0x03' | Set-Content "$dir\$Name-stats.txt"
}
# ASUS-side logs of the same period (the AIO HAL logs every function it runs, with timestamps)
$diag = 'C:\ProgramData\ASUS\ARMOURY CRATE Diagnosis'; $today = Get-Date -Format 'yyyy-MM-dd'
New-Item -ItemType Directory -Force "$dir\asus-logs" | Out-Null
foreach ($src in @("$diag\AacAIOFanSetup\$today.log", "$diag\ROG Live Service\ROGLiveService_$today.log", "$diag\ROG Live Service\deviceinfo.ini")) { if (Test-Path $src) { Copy-Item $src "$dir\asus-logs\" -Force } }
if (Test-Path "$diag\AIOFanSDK") { Copy-Item "$diag\AIOFanSDK\*" "$dir\asus-logs\" -Recurse -Force -ErrorAction SilentlyContinue }
Copy-Item 'C:\ProgramData\ASUS\Framework\aioFan\RYUJIN3\fp_1_config.xml' "$dir\asus-logs\fp_1_config.after.xml" -Force -ErrorAction SilentlyContinue
Copy-Item 'C:\ProgramData\ASUS\ArmourySDK\AIOFan\RYUJIN3\LastProfile.xml' "$dir\asus-logs\LastProfile.after.xml" -Force -ErrorAction SilentlyContinue
"`nFiles to bring back:"; Get-ChildItem $dir -Recurse | Format-Table -AutoSize FullName, Length, LastWriteTime
