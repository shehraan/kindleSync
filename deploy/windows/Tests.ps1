$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Core.ps1"

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw "FAIL: $Message" }
}
function Assert-Throws([scriptblock]$Operation, [string]$Message) {
    $caught = $false
    try { & $Operation | Out-Null } catch { $caught = $true }
    Assert-True $caught $Message
}

$configuration = [pscustomobject]@{
    Schema = 1
    Distro = 'Ubuntu-24.04'
    LinuxUser = 'shehr'
    LinuxRoot = '/home/shehr/My Books'
    WindowsUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    FrontendPort = 8787
    BackendPort = 8788
    InstanceId = '0123456789abcdef0123456789abcdef'
}
Assert-KoboSyncConfiguration $configuration
$arguments = Get-KoboSyncLaunchArguments $configuration
Assert-True ($arguments.Contains('"/home/shehr/My Books/deploy/run-server.sh"')) 'Linux paths containing spaces must stay one argument'
Assert-True ($arguments.EndsWith(' 8788')) 'Backend port must be passed explicitly'
Assert-True (-not $arguments.Contains('bash -c')) 'Launch the script directly, without a shell command string'

$bad = $configuration | Select-Object *
$bad.BackendPort = 8787
Assert-Throws { Assert-KoboSyncConfiguration $bad } 'Equal proxy/backend ports must be rejected'
$bad = $configuration | Select-Object *
$bad.LinuxRoot = "/home/shehr/a`nother"
Assert-Throws { Assert-KoboSyncConfiguration $bad } 'Multiline task arguments must be rejected'
$bad = $configuration | Select-Object *
$bad.InstanceId = ''
Assert-Throws { Assert-KoboSyncConfiguration $bad } 'Missing backend identity must be rejected'

$plan = Get-KoboSyncNetworkPlan $configuration
Assert-True ($plan.ListenAddress -eq '0.0.0.0' -and $plan.ListenPort -eq 8787) 'Public listener must use the configured frontend port'
Assert-True ($plan.ConnectAddress -eq '127.0.0.1' -and $plan.ConnectPort -eq 8788) 'Proxy must target Windows loopback, not a changing WSL IP'
Assert-True ($plan.RemoteAddress -eq 'LocalSubnet') 'Firewall scope must be limited to the local subnet'

$task = New-KoboSyncTaskDefinition $configuration
Assert-True ([int]$task.Principal.RunLevel -eq 0) 'Service task must run Limited, never elevated'
Assert-True ([int]$task.Principal.LogonType -eq 3) 'Use the interactive user token without storing a password'
$taskOwner = [string]$task.Principal.UserId
if (-not $taskOwner.StartsWith('S-1-')) {
    $taskOwner = ([Security.Principal.NTAccount]$taskOwner).Translate([Security.Principal.SecurityIdentifier]).Value
}
Assert-True ($taskOwner -eq $configuration.WindowsUserSid) 'Task must use the original Windows user'
Assert-True ($task.Settings.ExecutionTimeLimit -eq 'PT0S') 'Long-running service must have no execution time limit'
Assert-True ([int]$task.Settings.MultipleInstances -eq 2) 'Overlapping starts must be ignored'
Assert-True ($task.Settings.WakeToRun -and $task.Settings.StartWhenAvailable) 'Wake and restart catch-up must be requested'
Assert-True (-not $task.Settings.DisallowStartIfOnBatteries -and -not $task.Settings.StopIfGoingOnBatteries) 'Task must not silently stop on battery'
Assert-True (@($task.Triggers).Count -eq 2) 'Task needs logon and daily recovery triggers'
Assert-True (@($task.Triggers | Where-Object { $_.StartBoundary -match '^\d{4}-\d{2}-\d{2}T05:50:00$' }).Count -eq 1) 'Daily recovery must stay at 05:50 local across DST changes'

$owned = [pscustomobject]@{ Description = 'KoboSync WSL reading/audio service'; Actions = $task.Actions; Principal = $task.Principal }
Assert-KoboSyncTaskCompatible $owned $configuration
$foreign = [pscustomobject]@{ Description = 'Another application'; Actions = $task.Actions; Principal = $task.Principal }
Assert-Throws { Assert-KoboSyncTaskCompatible $foreign $configuration } 'An unrelated existing task must not be overwritten'

foreach ($parameter in @('Protocol', 'LocalPort', 'RemoteAddress', 'EdgeTraversalPolicy')) {
    Assert-True ((Get-Command Set-NetFirewallRule).Parameters.ContainsKey($parameter)) "Firewall update must support $parameter on this host"
}

foreach ($name in @('Install-Windows.ps1', 'Uninstall-Windows.ps1', 'Invoke-Install.ps1')) {
    $tokens = $null; $parseErrors = $null
    [Management.Automation.Language.Parser]::ParseFile("$PSScriptRoot\$name", [ref]$tokens, [ref]$parseErrors) | Out-Null
    Assert-True (@($parseErrors).Count -eq 0) "$name must parse without errors"
}
'PASS: Windows task, argument handling, firewall scope and ownership checks'
