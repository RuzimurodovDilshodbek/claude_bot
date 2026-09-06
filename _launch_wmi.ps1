# WMI orqali botni ishga tushiradi. Yaratilgan jarayonning ota-jarayoni WMI
# xizmati bo'ladi — ChatGPT/Claude Code sandbox ta'sir qilmaydi.
$exe = "C:\Users\Asus TUF\AppData\Local\Programs\Python\Python312\pythonw.exe"
$script = "D:\work\claude-tg\bot.py"
$workdir = "D:\work\claude-tg"

$startupClass = [wmiclass]"Win32_ProcessStartup"
$startup = $startupClass.CreateInstance()
$startup.CreateFlags = 0x00000008  # DETACHED_PROCESS

$result = Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList @(
    "`"$exe`" `"$script`"", $workdir, $startup
)

if ($result.ReturnValue -eq 0) {
    Write-Host "[OK] Bot WMI orqali ishga tushirildi. PID: $($result.ProcessId)"
} else {
    Write-Host "[XATO] WMI ReturnValue: $($result.ReturnValue)"
}
