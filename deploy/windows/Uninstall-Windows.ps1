[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ConfigPath)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Core.ps1"
if (-not (Test-KoboSyncAdministrator)) { throw 'Administrator approval is required to remove the network configuration' }
$configuration = Read-KoboSyncConfiguration $ConfigPath
$plan = Get-KoboSyncNetworkPlan $configuration
$task = Get-ScheduledTask -TaskName 'KoboSync' -ErrorAction SilentlyContinue
Assert-KoboSyncTaskCompatible $task $configuration
if ($task) {
    Stop-ScheduledTask -TaskName 'KoboSync'
    Unregister-ScheduledTask -TaskName 'KoboSync' -Confirm:$false
}
$rule = Get-NetFirewallRule -Name $plan.RuleName -ErrorAction SilentlyContinue
if ($rule -and $rule.Group -eq $plan.RuleGroup) { Remove-NetFirewallRule -Name $plan.RuleName }
if ((Get-KoboSyncProxyValue $plan) -eq "$($plan.ConnectAddress)/$($plan.ConnectPort)") {
    $netsh = Join-Path ([Environment]::GetFolderPath('System')) 'netsh.exe'
    & $netsh interface portproxy delete v4tov4 "listenaddress=$($plan.ListenAddress)" "listenport=$($plan.ListenPort)" protocol=tcp
    if ($LASTEXITCODE -ne 0) { throw 'Removing the port proxy failed' }
}
'KoboSync task and its matching network rules removed. Books and application data were preserved.'
