<#
실시간 수집기(core / network)를 Windows 작업 스케줄러에 등록한다.

    관리자 PowerShell 에서 실행:
        powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1

    해제:
        powershell -ExecutionPolicy Bypass -File scripts\unregister_tasks.ps1

3주간 무인 운영이 목표라, "죽지 않는 것"보다 "죽어도 알아서 되살아나는 것"에
비중을 뒀다. 다만 PC 를 종료하거나 절전으로 보내는 동작은 넣지 않는다.
#>

[CmdletBinding()]
param(
    # 감시(워치독) 반복 트리거를 끄고 요구된 트리거 2개만 등록한다.
    [switch]$NoWatchdog,
    # 워치독 반복 주기(분).
    [int]$WatchdogMinutes = 10,
    # 현재 사용자(S4U) 대신 SYSTEM 계정으로 실행한다.
    [switch]$UseSystemAccount
)

$ErrorActionPreference = "Stop"

$TaskPath   = "\CheonanBus\"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogsDir     = Join-Path $ProjectRoot "logs"
$Launcher    = Join-Path $PSScriptRoot "run_collector.cmd"
$Modes       = @("core", "network")

# --- 사전 점검 ---------------------------------------------------------------

$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[중단] 관리자 권한이 필요합니다." -ForegroundColor Red
    Write-Host "       '로그온 여부에 관계없이 실행' 을 켜려면 배치 로그온 권한 부여가 필요하고,"
    Write-Host "       그 작업은 관리자만 할 수 있습니다."
    Write-Host "       PowerShell 을 '관리자 권한으로 실행' 한 뒤 다시 시도하십시오."
    exit 1
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    $python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe"
}
if (-not (Test-Path $python)) {
    Write-Host "[중단] python.exe 를 찾지 못했습니다: $python" -ForegroundColor Red
    exit 1
}

$collector = Join-Path $ProjectRoot "src\collect_realtime.py"
foreach ($p in @($collector, (Join-Path $ProjectRoot ".env"),
                 (Join-Path $ProjectRoot "data\cheonan_routes_detail.csv"))) {
    if (-not (Test-Path $p)) {
        Write-Host "[중단] 필요한 파일이 없습니다: $p" -ForegroundColor Red
        exit 1
    }
}

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

# --- 런처 생성 ---------------------------------------------------------------
# 작업 스케줄러가 python.exe 를 직접 부르면 표준출력이 버려져, 파이썬이 예외로
# 죽었을 때 흔적이 남지 않는다. cmd 를 한 겹 두어 stdout/stderr 를 파일로 남긴다.
# (수집기 자체 로그와 별개로, 하드 크래시의 트레이스백을 잡기 위한 것)

$launcherBody = @"
@echo off
rem === scripts\register_tasks.ps1 이 생성한 파일입니다. 직접 고치지 마십시오. ===
setlocal
set "MODE=%~1"
if "%MODE%"=="" set "MODE=core"
cd /d "$ProjectRoot"
if not exist "logs" mkdir "logs"
echo [%DATE% %TIME%] launcher start mode=%MODE% >> "logs\task_%MODE%_stdio.log"
"$python" -u "src\collect_realtime.py" %MODE% >> "logs\task_%MODE%_stdio.log" 2>&1
echo [%DATE% %TIME%] launcher exit code=%ERRORLEVEL% >> "logs\task_%MODE%_stdio.log"
exit /b %ERRORLEVEL%
"@
Set-Content -Path $Launcher -Value $launcherBody -Encoding OEM
Write-Host "런처 생성: $Launcher"
Write-Host "  python : $python"

# --- 공통 설정 ---------------------------------------------------------------

if ($UseSystemAccount) {
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
                    -LogonType ServiceAccount -RunLevel Highest
    $who = "SYSTEM"
} else {
    # S4U: 비밀번호를 저장하지 않으면서 '로그온 여부에 관계없이 실행' 을 만족한다.
    $who = "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId $who `
                    -LogonType S4U -RunLevel Highest
}

function New-CollectorSettings {
    # -MultipleInstances IgnoreNew : 이미 돌고 있으면 새 인스턴스를 만들지 않는다
    # -AllowStartIfOnBatteries     : 배터리 전원에서도 시작한다
    # -DontStopIfGoingOnBatteries  : 배터리로 전환돼도 중지하지 않는다
    # -StartWhenAvailable          : 놓친 실행은 깨어난 뒤 따라잡는다
    # -WakeToRun                   : 실행 시각에 절전을 해제한다
    # -ExecutionTimeLimit Zero     : 기본 3일 제한 해제 (끝나도 강제 종료 안 함)
    # -RestartInterval/-RestartCount : 실패 시 1분 뒤 재시작
    $s = New-ScheduledTaskSettingsSet `
            -MultipleInstances IgnoreNew `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -WakeToRun `
            -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartInterval (New-TimeSpan -Minutes 1) `
            -RestartCount 999
    # 유휴 상태가 끝나도 중지하지 않는다 (기본값이 '중지' 라 반드시 꺼야 한다)
    $s.IdleSettings.StopOnIdleEnd = $false
    $s.IdleSettings.RestartOnIdle = $false
    $s.RunOnlyIfNetworkAvailable  = $false   # 네트워크 판정 실패로 안 도는 일 방지
    $s.Hidden = $false
    return $s
}

function New-CollectorTriggers {
    # ① 시스템 시작 시 (네트워크 스택이 올라올 여유로 1분 지연)
    $atStartup = New-ScheduledTaskTrigger -AtStartup
    $atStartup.Delay = "PT1M"

    # ② 매일 05:25 (수집 시간대 05:30 직전)
    $daily = New-ScheduledTaskTrigger -Daily -At "05:25"

    $triggers = @($atStartup, $daily)

    if (-not $NoWatchdog) {
        # ③ 워치독 — 요구사항에 없는 추가 트리거다. 이유는 아래.
        #
        # '실패 시 재시작' 은 작업이 0 이 아닌 종료 코드로 끝났을 때만 동작하고,
        # 재시도 횟수도 유한하다(999). 수집기가 종료 코드 0 으로 조용히 빠져나가거나
        # 재시도가 소진되면 그대로 멈춘 채 3주가 지나갈 수 있다.
        # N 분마다 시작을 시도하되 IgnoreNew 로 중복을 막으면, 어떤 이유로 멈추든
        # N 분 안에 되살아난다. 요구된 트리거 2개만 원하면 -NoWatchdog 를 준다.
        # 반복을 준 채로 만들어야 .Repetition 객체가 생긴다. 그 뒤 Duration 을
        # 비우면 '무기한' 이 된다. [TimeSpan]::MaxValue 를 넘기면
        # P99999999DT23H59M59S 로 직렬화돼 스케줄러가 거부한다.
        $wd = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
                -RepetitionInterval (New-TimeSpan -Minutes $WatchdogMinutes) `
                -RepetitionDuration (New-TimeSpan -Days 1)
        $wd.Repetition.Duration          = $null
        $wd.Repetition.StopAtDurationEnd = $false
        $triggers += $wd
    }
    return $triggers
}

# --- 등록 --------------------------------------------------------------------

foreach ($mode in $Modes) {
    $name = "collect_$mode"
    $action = New-ScheduledTaskAction -Execute $Launcher -Argument $mode `
                -WorkingDirectory $ProjectRoot

    $existing = Get-ScheduledTask -TaskPath $TaskPath -TaskName $name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "기존 작업 제거: $TaskPath$name"
        Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $name -Confirm:$false
    }

    Register-ScheduledTask -TaskPath $TaskPath -TaskName $name `
        -Action $action -Trigger (New-CollectorTriggers) `
        -Settings (New-CollectorSettings) -Principal $principal `
        -Description "천안 시내버스 실시간 수집기 ($mode 모드). 3주 상시 수집용." `
        -ErrorAction Stop | Out-Null

    # Register-ScheduledTask 는 실패해도 비종료 오류만 내는 경우가 있다.
    # 등록됐다고 말하기 전에 실제로 조회되는지 확인한다.
    $check = Get-ScheduledTask -TaskPath $TaskPath -TaskName $name -ErrorAction SilentlyContinue
    if (-not $check) {
        Write-Host "[중단] $TaskPath$name 등록에 실패했습니다." -ForegroundColor Red
        exit 1
    }
    Write-Host "등록 완료: $TaskPath$name  (실행 계정: $who)" -ForegroundColor Green
}

Write-Host ""
Write-Host "다음 명령으로 확인할 수 있습니다:"
Write-Host "  schtasks /query /tn `"${TaskPath}collect_core`" /v /fo list"
Write-Host "  schtasks /query /tn `"${TaskPath}collect_network`" /v /fo list"
Write-Host ""
Write-Host "지금 바로 시작:"
Write-Host "  schtasks /run /tn `"${TaskPath}collect_core`""
Write-Host "  schtasks /run /tn `"${TaskPath}collect_network`""
