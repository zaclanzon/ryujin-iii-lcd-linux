param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Text)
$s = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), ($Text -join ' ')
Add-Content 'C:\Claude\ryujin-capture\actions-log.txt' $s -Encoding utf8
Write-Host $s -ForegroundColor Cyan
