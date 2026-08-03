<#
수집기를 안전하게 재시작한다. (코드를 고친 뒤 반영할 때 쓴다)

    관리자 PowerShell 에서 실행:
        powershell -ExecutionPolicy Bypass -File scripts\restart_tasks.ps1

Stop-ScheduledTask 는 런처(cmd)만 종료하고 그 자식인 python 은 고아로 남겨두는
경우가 있다. 남은 프로세스는 옛 코드를 메모리에 들고 계속 돌기 때문에,
작업만 다시 시작하면 옛 프로세스와 새 프로세스가 동시에 도는 상태가 된다.
(MultipleInstances=IgnoreNew 는 작업 인스턴스만 막지 고아 프로세스는 못 막는다)
그래서 여기서는 반드시 프로세스가 사라진 것을 확인한 뒤 시작한다.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$TaskPath = "\CheonanBus\"
$Modes    = @("core", "network")

$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[중단] 관리자 권한이 필요합니다." -ForegroundColor Red
    Write-Host "       수집기는 RunLevel=Highest 로 돌기 때문에 일반 권한으로는 종료할 수 없습니다."
    exit 1
}

function Get-CollectorProcess {
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine -match "collect_realtime\.py" }
}

# --- 1. 작업 중지 ---
foreach ($mode in $Modes) {
    $name = "collect_$mode"
    if (-not (Get-ScheduledTask -TaskPath $TaskPath -TaskName $name -ErrorAction SilentlyContinue)) {
        Write-Host "[중단] 작업이 없습니다: $TaskPath$name. 먼저 register_tasks.ps1 을 실행하십시오." -ForegroundColor Red
        exit 1
    }
    try { Stop-ScheduledTask -TaskPath $TaskPath -TaskName $name } catch {}
    Write-Host "작업 중지 요청: $name"
}

# --- 2. 살아남은 프로세스 정리 ---
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    $left = @(Get-CollectorProcess)
    if ($left.Count -eq 0) { break }
    foreach ($p in $left) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host "  고아 프로세스 종료: PID $($p.ProcessId) (생성 $($p.CreationDate))"
        } catch {
            Write-Host "  종료 실패 PID $($p.ProcessId): $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
    Start-Sleep -Seconds 2
}

$left = @(Get-CollectorProcess)
if ($left.Count -gt 0) {
    Write-Host "[중단] 아직 살아 있는 수집기 프로세스가 있어 재시작하지 않습니다:" -ForegroundColor Red
    $left | ForEach-Object { Write-Host "  PID $($_.ProcessId)" }
    Write-Host "       중복 수집을 막기 위해 여기서 멈춥니다. 직접 종료한 뒤 다시 실행하십시오."
    exit 1
}
Write-Host "수집기 프로세스 없음 확인" -ForegroundColor Green

# --- 3. 재시작 ---
foreach ($mode in $Modes) {
    Start-ScheduledTask -TaskPath $TaskPath -TaskName "collect_$mode"
    Write-Host "시작 요청: collect_$mode"
}

# --- 4. 실제로 떴는지 확인 ---
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    if (@(Get-CollectorProcess).Count -ge $Modes.Count) { break }
    Start-Sleep -Seconds 3
}

$now = @(Get-CollectorProcess)
Write-Host ""
Write-Host "=== 결과 ==="
foreach ($mode in $Modes) {
    $t = Get-ScheduledTask -TaskPath $TaskPath -TaskName "collect_$mode"
    Write-Host ("{0,-18} State={1}" -f $t.TaskName, $t.State)
}
foreach ($p in $now) {
    $m = if ($p.CommandLine -match "collect_realtime\.py\s+(\w+)") { $Matches[1] } else { "?" }
    Write-Host ("  PID {0,-7} mode={1,-8} 생성={2}" -f $p.ProcessId, $m, $p.CreationDate)
}
if ($now.Count -lt $Modes.Count) {
    Write-Host "[경고] 수집기 프로세스가 $($now.Count)개뿐입니다. logs\task_*_stdio.log 를 확인하십시오." -ForegroundColor Yellow
    exit 1
}
Write-Host "재시작 완료" -ForegroundColor Green
