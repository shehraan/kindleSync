[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [switch]$PlanOnly,
    [switch]$SkipNetwork
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Core.ps1"
$configuration = Read-KoboSyncConfiguration $ConfigPath
$plan = Get-KoboSyncNetworkPlan $configuration
$definition = New-KoboSyncTaskDefinition $configuration
$taskName = 'KoboSync'

if ($PlanOnly) {
    [pscustomobject]@{
        TaskName = $taskName
        WindowsUserSid = $configuration.WindowsUserSid
        RunLevel = 'Limited'
        LogonType = 'Interactive; no password stored'
        Executable = $definition.Actions[0].Execute
        Arguments = $definition.Actions[0].Arguments
        Triggers = @('At logon', 'Daily 06:00 local')
        ExecutionTimeLimit = $definition.Settings.ExecutionTimeLimit
        WakeToRun = $definition.Settings.WakeToRun
        Network = $plan
    } | ConvertTo-Json -Depth 5
    exit 0
}

if (-not $SkipNetwork -and -not (Test-KoboSyncAdministrator)) {
    throw 'Administrator approval is required to add the local-subnet firewall rule and loopback port proxy'
}
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Assert-KoboSyncTaskCompatible $existingTask $configuration

# Detect a conflicting backend before starting WSL or exposing a listener.
Assert-KoboSyncBackendAvailable $configuration
$createdProxy = $false
$createdRule = $false
$createdTask = $false
$healthy = $false
try {
    if (-not $SkipNetwork) {
        $expectedProxy = "$($plan.ConnectAddress)/$($plan.ConnectPort)"
        $existingProxy = Get-KoboSyncProxyValue $plan
        if ($existingProxy -and $existingProxy -ne $expectedProxy) { throw 'Port proxy already belongs to another service' }
        $rule = Get-NetFirewallRule -Name $plan.RuleName -ErrorAction SilentlyContinue
        if ($rule -and $rule.Group -ne $plan.RuleGroup) { throw 'Firewall rule name already belongs to another service' }
        if (-not $existingProxy -and @(Get-NetTCPConnection -LocalPort $plan.ListenPort -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
            throw "TCP port $($plan.ListenPort) is already in use"
        }
    }

    # An alternate administrator may approve setup, but the task always uses
    # the original signed-in user's limited token and their WSL distribution.
    Register-ScheduledTask -TaskName $taskName -InputObject $definition -Force | Out-Null
    $createdTask = $null -eq $existingTask
    Start-ScheduledTask -TaskName $taskName
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (Test-KoboSyncHealth $configuration $configuration.BackendPort) { $healthy = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) { throw 'The intended WSL backend did not become ready; no LAN rules were added. Inspect data/server.log' }

    if (-not $SkipNetwork) {
        $helper = Get-Service iphlpsvc
        if ($helper.StartType -eq 'Disabled') { throw 'Windows IP Helper is disabled; an administrator must enable it before using portproxy' }
        if ($helper.StartType -ne 'Automatic') { Set-Service iphlpsvc -StartupType Automatic }
        if ($helper.Status -ne 'Running') { Start-Service iphlpsvc }
        $netsh = Join-Path ([Environment]::GetFolderPath('System')) 'netsh.exe'
        $nativeArguments = @('interface', 'portproxy', 'add', 'v4tov4',
            "listenaddress=$($plan.ListenAddress)", "listenport=$($plan.ListenPort)",
            "connectaddress=$($plan.ConnectAddress)", "connectport=$($plan.ConnectPort)", 'protocol=tcp')
        $message = (& $netsh @nativeArguments 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0) { throw "Port proxy failed: $message" }
        $createdProxy = -not $existingProxy
        if ($rule) {
            Set-NetFirewallRule -Name $plan.RuleName -Enabled True -Direction Inbound -Action Allow -Profile Any `
                -Protocol TCP -LocalPort $plan.ListenPort -RemoteAddress LocalSubnet -EdgeTraversalPolicy Block | Out-Null
        } else {
            New-NetFirewallRule -Name $plan.RuleName -DisplayName 'KoboSync home-network reading/audio sync' -Group $plan.RuleGroup `
                -Enabled True -Direction Inbound -Action Allow -Profile Any -Protocol TCP -LocalPort $plan.ListenPort `
                -RemoteAddress LocalSubnet -EdgeTraversalPolicy Block | Out-Null
            $createdRule = $true
        }
        $healthy = $false
        for ($attempt = 0; $attempt -lt 15; $attempt++) {
            if (Test-KoboSyncHealth $configuration $configuration.FrontendPort) { $healthy = $true; break }
            Start-Sleep -Seconds 1
        }
        if (-not $healthy) { throw 'The intended instance is not reachable through the port proxy' }
    }
} catch {
    # Remove only resources created by this invocation, preserving prior setup.
    if ($createdRule) { Remove-NetFirewallRule -Name $plan.RuleName -ErrorAction Continue }
    if ($createdProxy) {
        & $netsh interface portproxy delete v4tov4 "listenaddress=$($plan.ListenAddress)" "listenport=$($plan.ListenPort)" protocol=tcp | Out-Null
    }
    if ($createdTask) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction Continue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Continue
    }
    throw
}
$task = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
$result = [pscustomobject]@{
    InstalledAt = (Get-Date).ToUniversalTime().ToString('o')
    TaskName = $taskName
    TaskState = [string]$task.State
    LastTaskResult = $info.LastTaskResult
    NextRunTime = $info.NextRunTime.ToString('o')
    WindowsUserSid = $configuration.WindowsUserSid
    RunLevel = 'Limited'
    BackendPort = $configuration.BackendPort
    FrontendPort = $configuration.FrontendPort
    NetworkConfigured = -not $SkipNetwork
    InstanceId = $configuration.InstanceId
    HealthOk = $healthy
}
$resultPath = Join-Path (Split-Path -Parent $ConfigPath) 'install-result.json'
$result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $resultPath -Encoding UTF8
$result | ConvertTo-Json -Depth 4
if (-not $healthy) { throw 'Task installed, but health did not become ready; inspect data/server.log in the WSL checkout' }
