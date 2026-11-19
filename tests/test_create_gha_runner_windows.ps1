# Run with powershell.exe or pwsh -NoProfile -File tests/test_create_gha_runner_windows.ps1.
# Extract functions without executing setup or needing Windows service privileges.
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../scripts/create-gha-runner.bat'
$text = [IO.File]::ReadAllText($source)
$marker = '# POWERSHELL_' + 'PAYLOAD'
$payload = $text.Substring($text.IndexOf($marker) + $marker.Length)
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($payload, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$function = $ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -eq 'Wait-RunnerService'
}
if (-not $function) { throw 'Wait-RunnerService function not found.' }
. ([scriptblock]::Create($function.Extent.Text))

function Write-Host {
    param([string]$Object)
    $script:messages.Add($Object)
}
function Get-Service {
    param([string]$Name)
    if ($Name -ne 'test-runner') { throw 'Incorrect service name.' }
    return [pscustomobject]@{ Status = $script:states.Dequeue() }
}
function Start-Sleep { param([int]$Seconds) }
function Get-CimInstance {
    param([string]$ClassName)
    if ($script:failCim) { throw 'Simulated access denied' }
    return [pscustomobject]@{
        Name = 'test-runner'; State = 'Stopped'; ExitCode = 1067
        ServiceSpecificExitCode = 99; StartName = 'NT AUTHORITY\NETWORK SERVICE'
        PathName = 'C:\actions-runner\bin\RunnerService.exe'
    }
}
function Set-TestState([string[]]$States) {
    $script:states = New-Object 'Collections.Generic.Queue[string]'
    foreach ($state in $States) { $script:states.Enqueue($state) }
    $script:messages = New-Object 'Collections.Generic.List[string]'
    $script:failCim = $false
}

# Immediate startup, pending startup, and Windows recovery after an early stop.
foreach ($sequence in @(@('Running'), @('StartPending', 'StartPending', 'Running'), @('Stopped', 'StartPending', 'Running'))) {
    Set-TestState $sequence
    Wait-RunnerService 'test-runner' 'C:\actions-runner'
    if ($script:states.Count -ne 0 -or ($script:messages -join '\n') -notmatch 'is running') {
        throw 'Startup or recovery was not confirmed correctly.'
    }
}

# Exhausted deadline must preserve registration, return a failure, and provide
# useful diagnostics even when the service-details query is unavailable.
foreach ($failCim in @($false, $true)) {
    Set-TestState @('Stopped')
    $script:failCim = $failCim
    $failure = $null
    try { Wait-RunnerService 'test-runner' 'C:\actions-runner' 0 }
    catch { $failure = $_.Exception.Message }
    if ($failure -notmatch 'last observed state: Stopped' -or $failure -notmatch 'remains registered') {
        throw 'Startup failure was hidden or did not describe the state.'
    }
    $output = $script:messages -join '\n'
    if ($output -notmatch 'sc.exe queryex' -or $output -notmatch 'ActionsRunnerService' -or $output -notmatch '_diag') {
        throw 'Missing service failure diagnostics.'
    }
    if (-not $failCim -and $output -notmatch '1067') { throw 'Missing Windows exit code.' }
    if ($failCim -and $output -notmatch 'Could not read service details') { throw 'Missing diagnostics fallback.' }
}
[Console]::WriteLine('PASS: payload syntax, service startup/recovery, timeout, exit codes, and diagnostics fallback')
