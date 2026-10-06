param(
    [Parameter(Mandatory=$true)][string]$Profile,
    [string]$Bridge = "$PSScriptRoot\Qorgau-SecurityBridge.exe",
    [string]$OutputDirectory = "$env:LOCALAPPDATA\Qorgau-Security-Test"
)
$ErrorActionPreference = 'Stop'
$qorgauProfile = (Resolve-Path -LiteralPath $Profile).Path
if (-not (Test-Path -LiteralPath "$qorgauProfile\config.json")) { throw 'Сначала зарегистрируйте приложение студента.' }
if (-not (Test-Path -LiteralPath "$qorgauProfile\journal.json")) { throw 'Сначала назначьте браузерный сеанс.' }
if (-not (Test-Path -LiteralPath $Bridge)) { throw 'Не найден Qorgau-SecurityBridge.exe рядом со скриптом.' }
$qorgauSeb = Join-Path ${env:ProgramFiles} 'SafeExamBrowser\Application\SafeExamBrowser.exe'
if (-not (Test-Path -LiteralPath $qorgauSeb)) {
    throw 'Установите подписанный Safe Exam Browser со службой из https://safeexambrowser.org/download/ и повторите подготовку.'
}
$qorgauSignature = Get-AuthenticodeSignature -LiteralPath $qorgauSeb
if ($qorgauSignature.Status -ne 'Valid') { throw 'Подпись Safe Exam Browser не прошла проверку Windows.' }
$qorgauService = Get-Service -Name 'SafeExamBrowser' -ErrorAction SilentlyContinue
if (-not $qorgauService -or $qorgauService.Status -ne 'Running') { throw 'Служба Safe Exam Browser не запущена. Требуется установка сотрудником с правами администратора.' }
$qorgauSecret = Read-Host 'Задайте пароль сотрудника для выхода из SEB (минимум 12 символов)' -AsSecureString
$qorgauBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($qorgauSecret)
try {
    $qorgauPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($qorgauBstr)
    if ($qorgauPlain.Length -lt 12) { throw 'Пароль слишком короткий.' }
    $qorgauSha = [Security.Cryptography.SHA256]::Create()
    try { $qorgauHash = [BitConverter]::ToString($qorgauSha.ComputeHash([Text.Encoding]::UTF8.GetBytes($qorgauPlain))).Replace('-', '').ToLowerInvariant() }
    finally { $qorgauSha.Dispose() }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($qorgauBstr)
    $qorgauPlain = $null
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$qorgauHashFile = Join-Path $OutputDirectory 'staff-quit.sha256'
Set-Content -LiteralPath $qorgauHashFile -Value $qorgauHash -Encoding ascii
$qorgauConfig = Join-Path $OutputDirectory 'Qorgau-Test.seb'
$qorgauArgs = @('--data', ('"' + $qorgauProfile + '"'), '--output', ('"' + $qorgauConfig + '"'), '--quit-hash-file', ('"' + $qorgauHashFile + '"'))
$qorgauProcess = Start-Process -FilePath $Bridge -ArgumentList $qorgauArgs -WindowStyle Hidden -PassThru
Write-Output "Мост запущен: PID $($qorgauProcess.Id). Конфигурация: $qorgauConfig"
Write-Output 'Это испытательный профиль. STRICT ещё не подтверждён. Сохраните пароль сотрудника перед открытием .seb.'
Write-Output 'Сначала начните сеанс Qorgau, затем вручную откройте .seb. После проверки выйдите из SEB с паролем сотрудника; затем остановите указанный процесс моста.'
