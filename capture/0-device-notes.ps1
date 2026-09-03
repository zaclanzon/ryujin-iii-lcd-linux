#Requires -RunAsAdministrator
# 0-device-notes.ps1 -- records how Windows sees the ROG RYUJIN III (0B05:1AA2): device tree, drivers per
# interface, the WinUSB device-interface GUID of MI_00, and which ASUS processes/services are running.
# Safe to run any time; writes device-manager-notes.txt in C:\Claude\ryujin-capture.
$dir = 'C:\Claude\ryujin-capture'; $out = "$dir\device-manager-notes.txt"
$lines = @("DEVICE MANAGER DETAILS - ROG RYUJIN III (0B05:1AA2)  collected $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')", '')
$devs = Get-PnpDevice | Where-Object { $_.InstanceId -match 'VID_0B05&PID_1AA2' }
foreach ($d in $devs) {
  $lines += "[$($d.Class)] $($d.FriendlyName)  Status=$($d.Status)"
  $lines += "  InstanceId : $($d.InstanceId)"
  foreach ($k in 'DEVPKEY_Device_HardwareIds','DEVPKEY_Device_CompatibleIds','DEVPKEY_Device_Service','DEVPKEY_Device_DriverInfPath','DEVPKEY_Device_DriverVersion','DEVPKEY_Device_LocationPaths','DEVPKEY_Device_Parent') {
    $v = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName $k -ErrorAction SilentlyContinue).Data
    if ($v) { $lines += ('  {0,-34}: {1}' -f $k.Replace('DEVPKEY_Device_',''), ($v -join ' | ')) }
  }
  $lines += ''
}
$lines += '--- WinUSB device-interface GUIDs (MI_00, registry Device Parameters) ---'
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB\VID_0B05&PID_1AA2&MI_00' -ErrorAction SilentlyContinue | ForEach-Object {
  $p = Get-ItemProperty "$($_.PSPath)\Device Parameters" -ErrorAction SilentlyContinue
  $lines += "  $($_.PSChildName): DeviceInterfaceGUIDs = $($p.DeviceInterfaceGUIDs -join ', ')"
}
$lines += ''; $lines += '--- ASUS services ---'
Get-Service | Where-Object { $_.Name -match 'Armoury|Lighting|ROG|Asus' } | ForEach-Object { $lines += ('  {0,-30} {1}' -f $_.Name, $_.Status) }
$lines += ''; $lines += '--- ASUS processes ---'
Get-Process | Where-Object { $_.Name -match 'Armoury|asus|Lighting|ROG' } | Sort-Object Name -Unique | ForEach-Object { $lines += "  $($_.Name)  $($_.Path)" }
$lines += ''; $lines += '--- Armoury Crate AIO component versions (deviceinfo.ini) ---'
$ini = 'C:\ProgramData\ASUS\ARMOURY CRATE Diagnosis\ROG Live Service\deviceinfo.ini'
if (Test-Path $ini) { $lines += (Get-Content $ini | Select-String -Pattern 'RYUJIN|Version|FW' | ForEach-Object { '  ' + $_.Line }) }
$lines | Set-Content $out -Encoding utf8
Write-Host "wrote $out"; Get-Content $out
