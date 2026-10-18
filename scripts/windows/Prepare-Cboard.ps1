# Native Windows account-command installation; no registration or login.
[CmdletBinding()]
param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Cboard'))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$exitCode = 1
$transcriptStarted = $false
$logPath = $null
$oldNativePreference = $null
if (Test-Path Variable:PSNativeCommandUseErrorActionPreference) {
    $oldNativePreference = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
}
function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    $previousErrorPreference = $ErrorActionPreference
    try {
        # Native stderr is diagnostic output; the process exit code decides failure.
        $ErrorActionPreference = 'Continue'
        $global:LASTEXITCODE = 1
        & $Exe @Arguments
        $nativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($nativeExitCode -ne 0) { throw "Command failed ($nativeExitCode): $Exe" }
}
function Test-PythonCandidate([string]$Candidate) {
    # ProcessStartInfo bypasses PowerShell 5.1 native quoting and stderr-as-error conversion.
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $Candidate
    $process.StartInfo.Arguments = '-I -X utf8 -c "import sys,importlib.util;sys.exit(0 if sys.version_info >= (3,11) and importlib.util.find_spec(''venv'') and importlib.util.find_spec(''ensurepip'') else 1)"'
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    try {
        if (-not $process.Start()) { return $false }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(10000)) {
            $process.Kill()
            $null = $process.WaitForExit(3000)
            Write-Host ('Skipping Python (probe timed out): '+$Candidate)
            return $false
        }
        if ($process.ExitCode -eq 0) { return $true }
        $detail = $stderr.GetAwaiter().GetResult()
        if ($detail.Length -gt 2048) { $detail = $detail.Substring(0,2048) }
        Write-Host ('Skipping Python candidate: '+$Candidate+'; exit '+$process.ExitCode)
        if ($detail) { Write-Host $detail }
        return $false
    } catch {
        Write-Host ('Skipping Python candidate: '+$Candidate+'; '+$_.Exception.Message)
        return $false
    } finally {
        $process.Dispose()
    }
}
function Find-Python {
    $candidates = @()
    foreach ($name in @('python.exe','python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
    }
    $programs = Join-Path $env:LOCALAPPDATA 'Programs\Python'
    if (Test-Path -LiteralPath $programs) {
        $candidates += @(Get-ChildItem -LiteralPath $programs -Filter python.exe -Recurse -File | Select-Object -ExpandProperty FullName)
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if ($candidate -like '*\Microsoft\WindowsApps\*') { continue }
        if (Test-PythonCandidate $candidate) { return $candidate }
    }
    return $null
}
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Native Windows required.' }
    $logs = Join-Path $InstallRoot 'logs'
    $null = New-Item -ItemType Directory -Path $logs -Force
    $logPath = Join-Path $logs ('install-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8)+'.log')
    Start-Transcript -LiteralPath $logPath | Out-Null
    $transcriptStarted = $true
    Write-Host 'Installer build: win-20261018-r2'
    Write-Host ('Installation log: '+$logPath)

    $source = Join-Path $PSScriptRoot 'source'
    if (-not (Test-Path -LiteralPath (Join-Path $source 'pyproject.toml'))) { throw 'Extract the entire ZIP before running START-WINDOWS.cmd.' }
    $manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'MANIFEST.json') -Raw | ConvertFrom-Json
    foreach ($item in $manifest.files) {
        $file = Join-Path $source $item.path
        $boundary = [IO.Path]::GetFullPath($source).TrimEnd('\')+'\'
        if (-not [IO.Path]::GetFullPath($file).StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid manifest path.' }
        if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ine $item.sha256) { throw ('Source checksum mismatch: '+$item.path) }
    }
    $python = Find-Python
    if (-not $python) {
        if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) { throw 'Python 3.11+ and venv required; install Python from python.org, then rerun this file.' }
        Write-Host 'Installing Python 3.12 for this Windows user...'
        Invoke-Checked 'winget.exe' @('install','--id','Python.Python.3.12','--exact','--scope','user','--silent','--accept-package-agreements','--accept-source-agreements','--disable-interactivity')
        $python = Find-Python
        if (-not $python) { throw 'Python was not found after installation. Reopen this script after the Python installer finishes.' }
    }
    $destination = Join-Path $InstallRoot ('install-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
    $null = New-Item -ItemType Directory -Path $destination
    Copy-Item -LiteralPath $source -Destination (Join-Path $destination 'source') -Recurse
    $project = Join-Path $destination 'source'
    $venv = Join-Path $InstallRoot 'venv'
    if (-not (Test-Path -LiteralPath (Join-Path $venv 'Scripts\python.exe'))) {
        if (Test-Path -LiteralPath $venv) { throw 'Existing venv directory is incomplete; preserve or move it before retrying.' }
        Invoke-Checked $python @('-I','-m','venv',$venv)
    }
    $runtime = Join-Path $venv 'Scripts\python.exe'
    Invoke-Checked $runtime @('-I','-m','pip','install','--disable-pip-version-check','-r',(Join-Path $project 'requirements-runtime.lock'))
    Invoke-Checked $runtime @('-I','-m','pip','install','--disable-pip-version-check','--no-deps',$project)
    $manager = Join-Path $InstallRoot 'account-manager'
    $managerSource = Join-Path $project 'codex-environment'
    if (-not (Test-Path -LiteralPath $manager)) {
        Copy-Item -LiteralPath $managerSource -Destination $manager -Recurse
    } else {
        # Update code only. Registry, per-account settings, assets and history remain local.
        foreach ($name in @('bin','codex_env','vendor','tools')) {
            $target = Join-Path $manager $name
            if (Test-Path -LiteralPath $target) {
                Move-Item -LiteralPath $target -Destination ($target+'.backup-'+[Guid]::NewGuid().ToString('N'))
            }
            Copy-Item -LiteralPath (Join-Path $managerSource $name) -Destination $target -Recurse
        }
    }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    Write-Host 'Checking native Codex; downloading the official pinned package if missing (may take a few minutes)...'
    $nativeRaw = Invoke-Checked $runtime @('-X','utf8','-I',(Join-Path $PSScriptRoot 'install_native.py'),'--manager',$manager,'--destination',(Join-Path $destination 'native-codex'))
    $nativeCli = ConvertFrom-Json -InputObject ($nativeRaw -join "`n")
    $env:CENV_CLI = [string]$nativeCli
    $evidence = Join-Path $destination 'evidence'
    $null = New-Item -ItemType Directory -Path $evidence
    Invoke-Checked $runtime @('-X','utf8','-I',(Join-Path $PSScriptRoot 'selfcheck.py'),'--manager',$manager,'--output',(Join-Path $evidence 'runtime-checks.json'))
    Invoke-Checked $runtime @('-X','utf8',(Join-Path $manager 'bin\cenv'),'install')
    Invoke-Checked $runtime @('-X','utf8','-I',(Join-Path $PSScriptRoot 'configure.py'),'--manager',$manager,'--cli',[string]$nativeCli)
    $commandDirectory = Join-Path $HOME '.config\codex-environment\bin'
    [string]$userPath = [Environment]::GetEnvironmentVariable('Path','User')
    if (-not (@($userPath -split ';') -contains $commandDirectory)) {
        [Environment]::SetEnvironmentVariable('Path',($userPath.TrimEnd(';')+';'+$commandDirectory),'User')
    }
    $env:Path = $commandDirectory+';'+$env:Path
    $pwsh = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    $shellExe = if ($pwsh) { $pwsh.Source } else { $null }
    if ($shellExe) {
        try {
            Invoke-Checked $shellExe @('-NoProfile','-Command','if ($PSVersionTable.PSVersion -ge [version]"7.4") { exit 0 } else { exit 1 }')
        } catch { $shellExe = $null }
    }
    if (-not $shellExe) {
        Write-Host 'Preparing portable PowerShell 7 (no system installation)...'
        $packages = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'powershell-native.json') -Raw | ConvertFrom-Json
        $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'arm64' } else { 'x64' }
        $package = $packages.$architecture
        $download = Join-Path $destination 'powershell.zip'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $package.url -OutFile $download
        if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ine $package.sha256) { throw 'PowerShell checksum mismatch.' }
        $shellHome = Join-Path $destination 'powershell'
        Expand-Archive -LiteralPath $download -DestinationPath $shellHome
        Remove-Item -LiteralPath $download
        $shellExe = Join-Path $shellHome 'pwsh.exe'
    }
    $shellFile = Join-Path $HOME '.config\codex-environment\shell.ps1'
    $smoke = Join-Path $destination 'Check-Commands.ps1'
    $quotedShell = "'"+$shellFile.Replace("'","''")+"'"
    $smokeText = @"
`$ErrorActionPreference = 'Stop'
. $quotedShell
function global:CenvInstallAliasCheck {}
`$global:CenvAliases = @(`$global:CenvAliases) + @('CenvInstallAliasCheck')
. $quotedShell
if (Get-Command CenvInstallAliasCheck -ErrorAction SilentlyContinue) { throw 'Removed alias remained in the shell' }
foreach (`$name in @('cenv','chelp','clist','cnew','cdel','clogin','cuse','cdefault','crun','cusage','creset','cboard')) {
    if ((Get-Command `$name -ErrorAction Stop).CommandType -ne 'Function') { throw "Command not loaded: `$name" }
}
chelp
if (`$LASTEXITCODE -ne 0) { throw 'chelp failed' }
cusage --help
if (`$LASTEXITCODE -ne 0) { throw 'cusage --help failed' }
clist
if (`$LASTEXITCODE -ne 0) { throw 'clist failed' }
cboard --help
if (`$LASTEXITCODE -ne 0) { throw 'cboard --help failed' }
Write-Host 'Command checks passed. No account was registered or logged in.' -ForegroundColor Green
"@
    [IO.File]::WriteAllText($smoke,$smokeText,[Text.UTF8Encoding]::new($true))
    Invoke-Checked $shellExe @('-NoProfile','-ExecutionPolicy','Bypass','-File',$smoke)
    $launch = Join-Path $InstallRoot 'Open-Cboard.ps1'
    $launchText = ". $quotedShell`r`nchelp`r`nWrite-Host 'Installed. Try cusage or clist. Accounts were not added.'`r`n"
    [IO.File]::WriteAllText($launch,$launchText,[Text.UTF8Encoding]::new($true))
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Cboard Accounts.lnk'))
    $shortcut.TargetPath = $shellExe
    $shortcut.Arguments = '-NoLogo -NoExit -ExecutionPolicy Bypass -File "'+$launch+'"'
    $shortcut.WorkingDirectory = $HOME
    $shortcut.Save()
    @{ installed = $true; command_checks_passed = $true; accounts_added = 0; login_started = $false } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'installation.json') -Encoding UTF8
    Write-Host ('Installed account commands. Evidence: '+$evidence) -ForegroundColor Green
    Write-Host 'Opening a ready PowerShell window. Use cusage there; older open shells do not inherit child-process functions.'
    $startArguments = '-NoLogo -NoExit -ExecutionPolicy Bypass -File "'+$launch+'"'
    $readyWindow = Start-Process -FilePath $shellExe -ArgumentList $startArguments -PassThru
    Start-Sleep -Seconds 1
    if ($readyWindow.HasExited) { throw 'The ready PowerShell window closed unexpectedly.' }
    Write-Host 'CBOARD_INSTALL_OK - commands verified and PowerShell window started.' -ForegroundColor Green
    $exitCode = 0
} catch {
    Write-Host ('CBOARD_INSTALL_FAILED: '+$_.Exception.Message) -ForegroundColor Red
    Write-Host $_.InvocationInfo.PositionMessage
    Write-Host $_.ScriptStackTrace
} finally {
    if ($null -ne $oldNativePreference) { $PSNativeCommandUseErrorActionPreference = $oldNativePreference }
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
        Copy-Item -LiteralPath $logPath -Destination (Join-Path $InstallRoot 'logs\install-last.log') -Force
    }
}
exit $exitCode
