function Assert-KoboSyncConfiguration($Configuration) {
    if ($Configuration.Schema -ne 1) { throw 'Unsupported deployment configuration schema' }
    if ($Configuration.Distro -notmatch '^[A-Za-z0-9._-]+$') { throw 'Invalid WSL distribution name' }
    if ($Configuration.LinuxUser -notmatch '^[a-z_][a-z0-9_-]*$') { throw 'Invalid Linux user name' }
    $root = [string]$Configuration.LinuxRoot
    if (-not $root.StartsWith('/') -or $root.TrimEnd('/').Length -eq 0 -or
        $root.IndexOf([char]34) -ge 0 -or $root -match '[\x00-\x1f]') {
        throw 'LinuxRoot must be an absolute directory without quotes or control characters'
    }
    if ($Configuration.WindowsUserSid -notmatch '^S-1-[0-9-]+$') { throw 'Invalid original Windows user SID' }
    if ($Configuration.InstanceId -notmatch '^[a-f0-9]{32}$') { throw 'Expected a persistent KoboSync instance identity' }
    foreach ($property in @('FrontendPort', 'BackendPort')) {
        $value = $Configuration.$property
        if ($null -eq $value -or [int]$value -ne $value -or [int]$value -lt 1024 -or [int]$value -gt 65535) {
            throw "Invalid $property"
        }
    }
    if ($Configuration.FrontendPort -eq $Configuration.BackendPort) { throw 'Proxy and backend ports must differ' }
}

function Read-KoboSyncConfiguration([string]$Path) {
    $configuration = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    Assert-KoboSyncConfiguration $configuration
    return $configuration
}

function Get-KoboSyncLaunchArguments($Configuration) {
    Assert-KoboSyncConfiguration $Configuration
    $script = ([string]$Configuration.LinuxRoot).TrimEnd('/') + '/deploy/run-server.sh'
    # No command shell is involved: wsl.exe passes these arguments to bash.
    # WSL recognizes option flags only when they are unquoted in the native
    # command line. Distro/user are validated shell-free identifiers above.
    return ('--distribution {0} --user {1} --exec /bin/bash "{2}" {3}' -f
        $Configuration.Distro, $Configuration.LinuxUser, $script, $Configuration.BackendPort)
}

function Get-KoboSyncNetworkPlan($Configuration) {
    Assert-KoboSyncConfiguration $Configuration
    return [pscustomobject]@{
        ListenAddress = '0.0.0.0'
        ListenPort = [int]$Configuration.FrontendPort
        ConnectAddress = '127.0.0.1'
        ConnectPort = [int]$Configuration.BackendPort
        RemoteAddress = 'LocalSubnet'
        RuleName = 'KoboSync-Local'
        RuleGroup = 'KoboSync'
    }
}

function New-KoboSyncTaskDefinition($Configuration) {
    Assert-KoboSyncConfiguration $Configuration
    $executable = Join-Path ([Environment]::GetFolderPath('System')) 'wsl.exe'
    $action = New-ScheduledTaskAction -Execute $executable -Argument (Get-KoboSyncLaunchArguments $Configuration)
    $principal = New-ScheduledTaskPrincipal -UserId $Configuration.WindowsUserSid -LogonType Interactive -RunLevel Limited
    $daily = New-ScheduledTaskTrigger -Daily -At '05:50'
    # Omit the UTC suffix/offset so Task Scheduler keeps local wall time across
    # daylight-saving transitions instead of fixing today's UTC equivalent.
    $daily.StartBoundary = [DateTime]::Today.AddHours(5).AddMinutes(50).ToString('yyyy-MM-ddTHH:mm:ss')
    $triggers = @((New-ScheduledTaskTrigger -AtLogOn -User $Configuration.WindowsUserSid), $daily)
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -WakeToRun `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    return New-ScheduledTask -Action $action -Principal $principal -Trigger $triggers -Settings $settings `
        -Description 'KoboSync WSL reading/audio service'
}

function Assert-KoboSyncTaskCompatible($Task, $Configuration) {
    if ($null -eq $Task) { return }
    $owner = [string]$Task.Principal.UserId
    if (-not $owner.StartsWith('S-1-')) {
        $owner = ([Security.Principal.NTAccount]$owner).Translate([Security.Principal.SecurityIdentifier]).Value
    }
    $actions = @($Task.Actions)
    $executable = Join-Path ([Environment]::GetFolderPath('System')) 'wsl.exe'
    if ($Task.Description -ne 'KoboSync WSL reading/audio service' -or
        $owner -ne $Configuration.WindowsUserSid -or $actions.Count -ne 1 -or
        $actions[0].Execute -ne $executable -or
        $actions[0].Arguments -ne (Get-KoboSyncLaunchArguments $Configuration)) {
        throw 'An existing KoboSync task belongs to another setup; refusing to replace it'
    }
}

function Get-KoboSyncProxyValue($Plan) {
    $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\PortProxy\v4tov4\tcp'
    $name = "$($Plan.ListenAddress)/$($Plan.ListenPort)"
    return Get-ItemPropertyValue -LiteralPath $key -Name $name -ErrorAction SilentlyContinue
}

function Test-KoboSyncAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-KoboSyncHealth($Configuration, [int]$Port) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        return ($health.ok -eq $true -and $health.service -ceq 'kobosync' -and
            $health.instance_id -ceq $Configuration.InstanceId)
    } catch { return $false }
}

function Assert-KoboSyncBackendAvailable($Configuration) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Configuration.BackendPort -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0 -and -not (Test-KoboSyncHealth $Configuration $Configuration.BackendPort)) {
        throw "Backend TCP port $($Configuration.BackendPort) belongs to another service; no LAN rules were added"
    }
}
