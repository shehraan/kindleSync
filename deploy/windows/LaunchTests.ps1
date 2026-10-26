param(
    [string]$Distro = 'Ubuntu-24.04',
    [string]$LinuxUser = 'shehr'
)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\Core.ps1"
$testId = [Guid]::NewGuid().ToString('N')
$linuxRoot = "/tmp/kobosync launch $testId"
$uncRoot = "\\wsl.localhost\$Distro" + $linuxRoot.Replace('/', '\')
$outputRoot = Join-Path ([IO.Path]::GetTempPath()) "kobosync-launch-$testId"
try {
    New-Item -ItemType Directory -Path "$uncRoot\deploy" -Force | Out-Null
    New-Item -ItemType Directory -Path $outputRoot | Out-Null
    [IO.File]::WriteAllText("$uncRoot\deploy\run-server.sh", "#!/bin/bash`nprintf '%s\n' `"`$1`"`n", [Text.UTF8Encoding]::new($false))
    $configuration = [pscustomobject]@{
        Schema = 1; Distro = $Distro; LinuxUser = $LinuxUser; LinuxRoot = $linuxRoot
        WindowsUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        BackendPort = 8788; FrontendPort = 8787; InstanceId = '0123456789abcdef0123456789abcdef'
    }
    $definition = New-KoboSyncTaskDefinition $configuration
    $process = Start-Process -FilePath $definition.Actions[0].Execute -ArgumentList $definition.Actions[0].Arguments `
        -RedirectStandardOutput "$outputRoot\stdout" -RedirectStandardError "$outputRoot\stderr" -PassThru -Wait
    $output = [IO.File]::ReadAllText("$outputRoot\stdout").Trim()
    $errorText = [IO.File]::ReadAllText("$outputRoot\stderr")
    if ($process.ExitCode -ne 0 -or $output -ne '8788') {
        throw "Native WSL launch failed: exit=$($process.ExitCode), output=$output, error=$errorText"
    }
    'PASS: actual wsl.exe launches the intended script with spaces and passes its backend port'
} finally {
    if (Test-Path -LiteralPath $uncRoot) { Remove-Item -LiteralPath $uncRoot -Recurse -Force }
    if (Test-Path -LiteralPath $outputRoot) { Remove-Item -LiteralPath $outputRoot -Recurse -Force }
}
