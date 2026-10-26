$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Core.ps1"

# Real loopback TCP listeners, HTTP requests and Windows port enumeration.
# No scheduled tasks, firewall rules or port proxies are changed.
function Start-TestHealthServer([string]$Body) {
    $job = Start-Job -ArgumentList $Body -ScriptBlock {
        param($Body)
        $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
        $listener.Start()
        Write-Output $listener.LocalEndpoint.Port
        try {
            while ($true) {
                if (-not $listener.Pending()) { Start-Sleep -Milliseconds 50; continue }
                $client = $listener.AcceptTcpClient()
                try {
                    $client.ReceiveTimeout = 2000
                    $stream = $client.GetStream()
                    $reader = [IO.StreamReader]::new($stream)
                    while ($reader.ReadLine()) { }
                    $bytes = [Text.Encoding]::UTF8.GetBytes($Body)
                    $header = [Text.Encoding]::ASCII.GetBytes("HTTP/1.1 200 OK`r`nContent-Type: application/json`r`nContent-Length: $($bytes.Length)`r`nConnection: close`r`n`r`n")
                    $stream.Write($header, 0, $header.Length)
                    $stream.Write($bytes, 0, $bytes.Length)
                } finally { $client.Dispose() }
            }
        } finally { $listener.Stop() }
    }
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if ($job.ChildJobs[0].Output.Count -gt 0) {
            return [pscustomobject]@{ Job = $job; Port = [int]$job.ChildJobs[0].Output[0] }
        }
        if ($job.State -eq 'Failed') { throw 'Test HTTP listener failed to start' }
        Start-Sleep -Milliseconds 100
    }
    Stop-Job $job; Remove-Job $job
    throw 'Test HTTP listener did not become ready'
}

$configuration = [pscustomobject]@{ InstanceId = '0123456789abcdef0123456789abcdef' }
foreach ($case in @(
    @{ Body = '{"ok":true}'; Accept = $false },
    @{ Body = '{"ok":true,"service":"kobosync","instance_id":"ffffffffffffffffffffffffffffffff"}'; Accept = $false },
    @{ Body = '{"ok":true,"service":"kobosync","instance_id":"0123456789abcdef0123456789abcdef"}'; Accept = $true }
)) {
    $server = Start-TestHealthServer $case.Body
    try {
        $configuration | Add-Member -NotePropertyName BackendPort -NotePropertyValue $server.Port -Force
        $accepted = $true
        try { Assert-KoboSyncBackendAvailable $configuration } catch { $accepted = $false }
        if ($accepted -ne $case.Accept) { throw "Incorrect backend ownership decision for $($case.Body)" }
        if ((Test-KoboSyncHealth $configuration $server.Port) -ne $case.Accept) {
            throw 'Readiness must require the same exact service identity as port ownership'
        }
    } finally {
        Stop-Job $server.Job
        Remove-Job $server.Job
    }
}
'PASS: unrelated service and other-library listeners rejected; intended instance accepted'
