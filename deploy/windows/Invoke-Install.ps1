param([switch]$PlanOnly)
$ErrorActionPreference = 'Stop'
$attemptId = [Guid]::NewGuid().ToString('N')
$logPath = Join-Path $PSScriptRoot "install-$attemptId.log"
$exitCode = 1
try {
    # A separate file per attempt avoids console selection blocking progress
    # and avoids a previous console retaining an exclusive transcript handle.
    & "$PSScriptRoot\Install-Windows.ps1" -ConfigPath "$PSScriptRoot\config.json" -PlanOnly:$PlanOnly *> $logPath
    $exitCode = 0
    $result = @{ Ok = $true }
} catch {
    $result = @{ Ok = $false; Error = $_.Exception.Message }
    ($_ | Out-String) | Add-Content -LiteralPath $logPath
}
$result.ProcessId = $PID
$result.PlanOnly = [bool]$PlanOnly
$result.LogPath = $logPath
$result.CompletedAt = (Get-Date).ToUniversalTime().ToString('o')
$result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'elevation-result.json') -Encoding UTF8
exit $exitCode
