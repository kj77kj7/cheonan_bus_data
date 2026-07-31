<#
register_tasks.ps1 이 등록한 작업을 해제한다.

    관리자 PowerShell 에서 실행:
        powershell -ExecutionPolicy Bypass -File scripts\unregister_tasks.ps1

기본적으로 돌고 있는 프로세스도 함께 멈춘다. 작업만 지우고 지금 도는 수집기는
그대로 두려면 -KeepRunning 을 준다.
#>

[CmdletBinding()]
param(
    # 실행 중인 수집기 프로세스는 건드리지 않는다.
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"

$TaskPath = "\CheonanBus\"
$Modes    = @("core", "network")

$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[중단] 관리자 권한이 필요합니다. PowerShell 을 관리자로 실행하십시오." -ForegroundColor Red
    exit 1
}

foreach ($mode in $Modes) {
    $name = "collect_$mode"
    $task = Get-ScheduledTask -TaskPath $TaskPath -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "없음(건너뜀): $TaskPath$name"
        continue
    }

    if (-not $KeepRunning) {
        try { Stop-ScheduledTask -TaskPath $TaskPath -TaskName $name } catch {}
    }
    Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $name -Confirm:$false
    Write-Host "해제 완료: $TaskPath$name" -ForegroundColor Green
}

# 비어 있으면 작업 폴더도 정리한다
try {
    $folder = (New-Object -ComObject "Schedule.Service")
    $folder.Connect()
    $root = $folder.GetFolder("\")
    $sub  = $root.GetFolder($TaskPath.TrimEnd("\"))
    if ($sub.GetTasks(0).Count -eq 0) {
        $root.DeleteFolder($TaskPath.TrimEnd("\"), 0)
        Write-Host "작업 폴더 삭제: $TaskPath"
    }
} catch {
    # 폴더 정리는 실패해도 무해하다
}

if (-not $KeepRunning) {
    # 스케줄러가 놓친 자식 프로세스(python)를 확실히 정리한다
    $killed = 0
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | ForEach-Object {
        if ($_.CommandLine -and $_.CommandLine -match "collect_realtime\.py") {
            try { Stop-Process -Id $_.ProcessId -Force; $killed++ } catch {}
        }
    }
    Write-Host "종료한 수집기 프로세스: $killed 개"
}

Write-Host ""
Write-Host "수집된 데이터(data\realtime\, logs\)는 지우지 않았습니다."
