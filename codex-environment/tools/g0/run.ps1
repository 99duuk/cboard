# G0 probes only. No account registration, profile editing, login or network installation.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$Cli,
    [string]$Node,
    [string]$CodexJs,
    [string]$NpmJs,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($PSVersionTable.PSVersion -lt [version]'7.4' -or -not $IsWindows) {
    throw 'Windows native PowerShell 7.4+ required; no probe or installation changes performed.'
}
$Python = (Resolve-Path -LiteralPath $Python).Path
if ([IO.Path]::GetExtension($Python) -ine '.exe') {
    throw 'Supply an explicit Python .exe path, not a command string or batch shim.'
}
$version = & $Python -I -c 'import json,sys; print(json.dumps(list(sys.version_info[:3])))'
if ($LASTEXITCODE -ne 0) { throw 'Python version inspection failed.' }
$parts = ConvertFrom-Json -InputObject ($version -join "`n")
if ($parts[0] -lt 3 -or ($parts[0] -eq 3 -and $parts[1] -lt 11)) {
    throw 'Python 3.11+ required.'
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path (Get-Location) ('g0-report-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
}
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Output directory already exists; choose a fresh path.' }
$null = New-Item -ItemType Directory -Path $OutputDirectory
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
$Stage = Join-Path ([IO.Path]::GetTempPath()) ('cenv-g0-'+[Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $Stage
$OldArgumentPassing = $PSNativeCommandArgumentPassing
$OldNativeErrorPreference = $PSNativeCommandUseErrorActionPreference
$PSNativeCommandUseErrorActionPreference = $false
$OldEncoding = [Console]::OutputEncoding
$PSNativeCommandArgumentPassing = 'Standard'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$FinalStatus = 1
try {
    $launcherInfo = & $Python -I (Join-Path $PSScriptRoot 'argv_probe.py') --prepare (Join-Path $Stage 'launcher')
    if ($LASTEXITCODE -ne 0) { throw 'Temporary console-entry preparation failed.' }
    $launcher = ConvertFrom-Json -InputObject ($launcherInfo -join "`n")
    $script:G0ProbeLauncher = [string]$launcher.launcher
    $script:G0ProbeExitCode = -1
    function Invoke-CenvG0Argv { & $script:G0ProbeLauncher @args; $script:G0ProbeExitCode = $LASTEXITCODE }
    $sentinel = Join-Path $Stage 'UNEXPECTED_CODE_EXECUTION'
    $expected = @('', 'two words', '한글 경로', 'embedded"quote', "single'quote", 'C:\trailing\', '&|<>^%!$;', '--', ('line1'+"`n"+'line2'), ('$(New-Item -ItemType File -Path "'+$sentinel+'")'))
    $raw = Invoke-CenvG0Argv @expected
    $received = ConvertFrom-Json -InputObject ($raw -join "`n") -NoEnumerate
    $same = $script:G0ProbeExitCode -eq 0 -and $expected.Count -eq $received.Count
    if ($same) {
        for ($i = 0; $i -lt $expected.Count; $i++) {
            if ($expected[$i] -cne $received[$i]) { $same = $false; break }
        }
    }
    $same = $same -and -not (Test-Path -LiteralPath $sentinel)
    $shell = @{
        schema_version = 1
        environment = @{ os = 'Windows'; powershell_version = $PSVersionTable.PSVersion.ToString() }
        checks = @{ T03 = @{
            status = $(if ($same) { 'partial' } else { 'fail' })
            observations = @('PowerShell function -> temporary console .exe -> Python native recorder', 'Not the final production manager launcher; final packaging validation remains required')
            argv_roundtrip_passed = $same
            case_count = $expected.Count
            production_launcher = $false
            official_cli_argv = 'not_run'
        } }
    }
    $shellPath = Join-Path $OutputDirectory 'powershell.json'
    [IO.File]::WriteAllText($shellPath, ($shell | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    $probeArgs = @('-I', (Join-Path $PSScriptRoot 'windows_probe.py'), '--output', (Join-Path $OutputDirectory 'windows.json'), '--shell-report', $shellPath)
    foreach ($entry in @(@('--cli',$Cli), @('--node',$Node), @('--codex-js',$CodexJs), @('--npm-js',$NpmJs))) {
        if ($entry[1]) { $probeArgs += @($entry[0], [string]$entry[1]) }
    }
    & $Python @probeArgs
    $probeExit = $LASTEXITCODE
    $processProbe = Join-Path $PSScriptRoot 'process_probe.py'
    if (Test-Path -LiteralPath $processProbe) {
        & $Python -I $processProbe --output (Join-Path $OutputDirectory 'process.json')
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Process probe incomplete; inspect process.json.' }
    }
    $windowsReport = Get-Content -LiteralPath (Join-Path $OutputDirectory 'windows.json') -Raw | ConvertFrom-Json
    $checks = @{}
    foreach ($id in @('T01','T02','T03','T04','T05','T06')) { $checks[$id] = $windowsReport.checks.$id }
    $processPath = Join-Path $OutputDirectory 'process.json'
    if (Test-Path -LiteralPath $processPath) {
        $processReport = Get-Content -LiteralPath $processPath -Raw | ConvertFrom-Json
        if ($processReport.schema_version -ne 1 -or $processReport.environment.os -ne 'Windows') {
            throw 'Invalid process evidence environment.'
        }
        $checks['T05'] = $processReport.checks.T05
    }
    $incomplete = @($checks.Keys | Where-Object { $checks[$_].status -ne 'pass' } | Sort-Object)
    $gate = @{
        schema_version = 1
        stage = 'G0'
        g0_passed = ($same -and $incomplete.Count -eq 0 -and $probeExit -eq 0)
        environment = $windowsReport.environment
        checks = $checks
        incomplete = $incomplete
        note = 'Partial observations do not establish full G0 or production support.'
    }
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'gate.json'), ($gate | ConvertTo-Json -Depth 32), [Text.UTF8Encoding]::new($false))
    Write-Host ('Evidence directory: '+$OutputDirectory)
    Write-Host ('G0 passed: '+$gate.g0_passed+'; incomplete: '+($incomplete -join ', '))
    $FinalStatus = $(if ($gate.g0_passed) { 0 } else { 1 })
}
finally {
    $PSNativeCommandArgumentPassing = $OldArgumentPassing
    $PSNativeCommandUseErrorActionPreference = $OldNativeErrorPreference
    [Console]::OutputEncoding = $OldEncoding
    Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Stage) { Write-Warning ('Temporary launcher cleanup incomplete: '+$Stage) }
    Remove-Item Function:Invoke-CenvG0Argv -ErrorAction SilentlyContinue
    Remove-Variable G0ProbeLauncher,G0ProbeExitCode -Scope Script -ErrorAction SilentlyContinue
}
exit $FinalStatus
