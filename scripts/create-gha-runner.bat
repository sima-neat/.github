@echo off
setlocal DisableDelayedExpansion
rem Windows PowerShell 5.1 is included with Windows. Keep this file intact:
rem the batch launcher executes the embedded PowerShell below without temp files.
set "GHA_SETUP_FILE=%~f0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$text = [IO.File]::ReadAllText($env:GHA_SETUP_FILE); $marker = '# POWERSHELL_' + 'PAYLOAD'; & ([ScriptBlock]::Create($text.Substring($text.IndexOf($marker) + $marker.Length)))"
exit /b %errorlevel%
# POWERSHELL_PAYLOAD
# Windows counterpart of create-gha-runner.sh. No Bash, jq, or unzip required.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Read-Default([string]$Prompt, [string]$Default) {
    $value = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value.Trim()
}

function Read-YesNo([string]$Prompt, [string]$Default) {
    while ($true) {
        $value = (Read-Default "$Prompt (y/n)" $Default).ToLowerInvariant()
        if ($value -in @('y', 'n')) { return ($value -eq 'y') }
        Write-Host 'Please answer y or n.'
    }
}

function Get-RunnerToken([string]$Endpoint) {
    $headers = @{
        Accept = 'application/vnd.github+json'
        Authorization = "Bearer $script:ApiToken"
        'X-GitHub-Api-Version' = '2022-11-28'
        'User-Agent' = 'sima-neat-windows-runner-setup'
    }
    try {
        $response = Invoke-RestMethod -Method Post -Uri $Endpoint -Headers $headers
    } catch {
        $status = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 'unavailable' }
        throw "GitHub API POST failed: $Endpoint (HTTP $status). Check ORG_ADMIN_TOKEN and its runner administration permissions. $($_.ErrorDetails.Message)"
    } finally {
        $headers.Clear()
    }
    if ([string]::IsNullOrWhiteSpace($response.token)) {
        throw "GitHub did not return a runner token from $Endpoint."
    }
    return $response.token
}

function Save-Download([string]$Uri, [string]$Destination) {
    # Rename only completed downloads so a failed transfer is not reused next time.
    $partial = "$Destination.partial"
    try {
        for ($attempt = 1; $attempt -le 4; $attempt++) {
            try {
                Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $partial -TimeoutSec 300
                Move-Item -LiteralPath $partial -Destination $Destination -Force
                return
            } catch {
                if ($attempt -eq 4) { throw }
                Start-Sleep -Seconds 2
            }
        }
    } finally {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-AwsCli {
    if (Get-Command aws.exe -ErrorAction SilentlyContinue) { return }
    $awsDirectory = Join-Path $env:ProgramFiles 'Amazon\AWSCLIV2'
    if (Test-Path -LiteralPath (Join-Path $awsDirectory 'aws.exe')) {
        $env:PATH = "$awsDirectory;$env:PATH"
        return
    }
    if (-not (Read-YesNo 'AWS CLI is missing. Install AWS CLI v2 (requires administrator approval)' 'y')) {
        throw 'Install AWS CLI v2 and re-run this script.'
    }
    $tempDirectory = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempDirectory | Out-Null
    try {
        $msi = Join-Path $tempDirectory 'AWSCLIV2.msi'
        Write-Host 'Downloading AWS CLI v2 for Windows (x64; ARM64 requires x64 emulation)...'
        Save-Download 'https://awscli.amazonaws.com/AWSCLIV2.msi' $msi
        $signature = Get-AuthenticodeSignature -LiteralPath $msi
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O="?Amazon Web Services,? Inc\."?(?:,|$)') {
            throw 'AWS CLI installer has an invalid or unexpected publisher signature.'
        }
        $installer = Start-Process msiexec.exe -Verb RunAs -Wait -PassThru -ArgumentList "/i `"$msi`" /qn /norestart"
        if ($installer.ExitCode -notin @(0, 3010)) {
            throw "AWS CLI installation failed with exit code $($installer.ExitCode)."
        }
        $env:PATH = "$awsDirectory;$env:PATH"
        if (-not (Get-Command aws.exe -ErrorAction SilentlyContinue)) {
            throw 'AWS CLI was not found after installation. Open a new terminal and re-run.'
        }
        if ($installer.ExitCode -eq 3010) { Write-Host 'AWS CLI installation requests a Windows restart.' }
    } finally {
        Remove-Item -LiteralPath $tempDirectory -Recurse -Force
    }
}

function Invoke-Runner([string[]]$RunnerArguments) {
    # config.cmd delegates to this executable. Calling it directly avoids cmd.exe
    # interpreting shell characters in prompted values and passes arguments intact.
    & '.\bin\Runner.Listener.exe' @RunnerArguments
    if ($LASTEXITCODE -ne 0) { throw "Runner command failed with exit code $LASTEXITCODE." }
}

$exitCode = 0
$originalLocation = Get-Location
$script:ApiToken = $env:ORG_ADMIN_TOKEN
Remove-Item Env:ORG_ADMIN_TOKEN -ErrorAction SilentlyContinue
try {
    Write-Host 'GitHub Actions Runner Setup for Windows'
    if ([string]::IsNullOrWhiteSpace($script:ApiToken)) {
        if ([Console]::IsInputRedirected) { throw 'ORG_ADMIN_TOKEN is required.' }
        $secureToken = Read-Host 'ORG_ADMIN_TOKEN (input hidden)' -AsSecureString
        $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        try { $script:ApiToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer) }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
            $secureToken.Dispose()
        }
    }
    if ([string]::IsNullOrWhiteSpace($script:ApiToken)) { throw 'ORG_ADMIN_TOKEN is required.' }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $scope = (Read-Default 'Runner scope: org or repo' 'org').ToLowerInvariant()
    if ($scope -notin @('org', 'repo')) { throw 'Invalid scope. Use org or repo.' }
    $org = Read-Default 'GitHub organization' 'sima-neat'
    if ($org -notmatch '^[a-zA-Z0-9][a-zA-Z0-9-]*$') { throw 'Invalid organization name.' }
    $url = "https://github.com/$org"
    $apiBase = "https://api.github.com/orgs/$org/actions/runners"
    if ($scope -eq 'repo') {
        $repo = Read-Default 'Repository name (without org prefix)' '.github'
        if ($repo -notmatch '^[a-zA-Z0-9_.-]+$') { throw 'Invalid repository name.' }
        $url = "$url/$repo"
        $apiBase = "https://api.github.com/repos/$org/$repo/actions/runners"
    }
    if ($env:RUNNER_PLATFORM -and $env:RUNNER_PLATFORM -ne 'win') {
        throw 'This script supports Windows only. Unset RUNNER_PLATFORM or set it to win.'
    }
    $nativeArch = $env:PROCESSOR_ARCHITEW6432
    if (-not $nativeArch) { $nativeArch = $env:PROCESSOR_ARCHITECTURE }
    $arch = switch ($nativeArch) {
        'AMD64' { 'x64' }
        'ARM64' { 'arm64' }
        'x86' { 'x86' }
        default { throw "Unsupported processor architecture: $nativeArch" }
    }
    if ($env:RUNNER_ARCH) { $arch = $env:RUNNER_ARCH.ToLowerInvariant() }
    if ($arch -notin @('x64', 'arm64', 'x86')) { throw 'Set RUNNER_ARCH to x64, arm64, or x86.' }
    Write-Host "Detected runner target: win-$arch"

    $suffix = [guid]::NewGuid().ToString('N').Substring(0, 6)
    $runnerName = Read-Default 'Runner name' "$env:COMPUTERNAME-win-$arch-$suffix"
    $labels = Read-Default 'Runner labels (comma-separated)' "self-hosted,Windows,$arch"
    $work = Read-Default 'Runner work folder' '_work'
    $ephemeral = Read-YesNo 'Ephemeral runner' 'n'
    $replace = Read-YesNo 'Replace existing runner with same name' 'y'
    $safeName = $runnerName -replace '[^a-zA-Z0-9_.-]', '-'
    $installRoot = Read-Default 'Install directory' (Join-Path (Get-Location).Path "actions-runner-$org-$safeName")
    $installRoot = [IO.Path]::GetFullPath($installRoot)
    $mode = (Read-Default 'Start mode: auto, service, background, foreground, none' 'auto').ToLowerInvariant()
    if ($mode -notin @('auto', 'service', 'background', 'foreground', 'none')) { throw 'Invalid start mode.' }
    if ($mode -eq 'auto') { $mode = if ($isAdmin) { 'service' } else { 'background' } }
    if ($mode -eq 'service' -and -not $isAdmin) { throw 'Service mode requires running this batch file as administrator.' }
    Write-Host "Resolved start mode: $mode"

    $runnerGroup = $null
    if ($scope -eq 'org') {
        $groupDefault = if ($env:RUNNER_GROUP) { $env:RUNNER_GROUP } else { 'Default' }
        $runnerGroup = Read-Default 'Runner group name' $groupDefault
    }
    if (Test-Path -LiteralPath $installRoot) {
        if (-not (Test-Path -LiteralPath $installRoot -PathType Container)) { throw 'Install directory is a file.' }
        if (Get-ChildItem -LiteralPath $installRoot -Force | Select-Object -First 1) {
            if ((Test-Path -LiteralPath "$installRoot\.runner") -and (Test-Path -LiteralPath "$installRoot\config.cmd")) {
                $action = (Read-Default 'Existing runner setup found. Action: skip or recreate' 'skip').ToLowerInvariant()
                if ($action -eq 'skip') { Write-Host "Skipping setup. Existing runner left unchanged at: $installRoot"; exit 0 }
                if ($action -ne 'recreate') { throw 'Invalid action. Use skip or recreate.' }
            } elseif (-not (Read-YesNo 'Install directory exists and is not empty. Continue anyway' 'n')) {
                throw 'Aborted.'
            }
        }
    }

    Ensure-AwsCli
    New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    Set-Location -LiteralPath $installRoot
    if (Test-Path -LiteralPath '.runner') {
        if ((Test-Path -LiteralPath '.service') -and -not $isAdmin) {
            throw 'Recreating a service runner requires running this batch file as administrator.'
        }
        if (Test-Path -LiteralPath '.service') {
            $serviceName = (Get-Content -LiteralPath '.service' -Raw).Trim()
            $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
            if ($service) {
                $service | Stop-Service
                $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
            }
        }
        $listenerPath = Join-Path $installRoot 'bin\Runner.Listener.exe'
        $running = Get-CimInstance Win32_Process -Filter "Name = 'Runner.Listener.exe'" |
            Where-Object { $_.ExecutablePath -eq $listenerPath }
        if ($running) { throw 'Stop the existing runner console before recreating this runner.' }
        Write-Host 'De-registering existing runner...'
        $removeToken = Get-RunnerToken "$apiBase/remove-token"
        try { Invoke-Runner @('remove', '--token', $removeToken) }
        finally { $removeToken = $null }
    }
    if (-not (Test-Path -LiteralPath 'config.cmd')) {
        Write-Host "Resolving latest runner package for win-$arch..."
        $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/actions/runner/releases/latest' -Headers @{ 'User-Agent' = 'sima-neat-windows-runner-setup' }
        $asset = $release.assets | Where-Object { $_.name -match "^actions-runner-win-$arch-[0-9.]+\.zip$" } | Select-Object -First 1
        if (-not $asset) { throw "Could not find runner download for win-$arch." }
        $archive = Join-Path $installRoot $asset.name
        if (-not (Test-Path -LiteralPath $archive)) { Save-Download $asset.browser_download_url $archive }
        if ($asset.digest) {
            if ($asset.digest -notmatch '^sha256:([a-fA-F0-9]{64})$') { throw 'Unsupported runner archive digest.' }
            $expectedHash = $Matches[1]
            $actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
            if ($actualHash -ne $expectedHash) { throw "Checksum mismatch for $($asset.name). Delete the archive and re-run." }
        } else { Write-Host 'No SHA256 digest published; validating ZIP format during extraction only.' }
        Write-Host "Extracting $($asset.name)..."
        Expand-Archive -LiteralPath $archive -DestinationPath $installRoot -Force
    }
    Get-ChildItem -LiteralPath $installRoot -File | Unblock-File
    Write-Host 'Requesting GitHub Actions runner registration token...'
    $registrationToken = Get-RunnerToken "$apiBase/registration-token"
    $configArguments = @('configure', '--unattended', '--url', $url, '--token', $registrationToken,
        '--name', $runnerName, '--work', $work, '--labels', $labels)
    if ($scope -eq 'org') { $configArguments += @('--runnergroup', $runnerGroup) }
    if ($ephemeral) { $configArguments += '--ephemeral' }
    if ($replace) { $configArguments += '--replace' }
    if ($mode -eq 'service') { $configArguments += '--runasservice' }
    Write-Host "Configuring runner for $url..."
    try { Invoke-Runner $configArguments }
    finally { $registrationToken = $null; $configArguments = $null; $script:ApiToken = $null }

    switch ($mode) {
        'service' {
            $serviceName = (Get-Content -LiteralPath '.service' -Raw).Trim()
            $service = Get-Service -Name $serviceName
            $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
            Write-Host "Runner Windows service started: $serviceName"
        }
        'background' {
            # A separate console runs GitHub's wrapper, including its update loop.
            Start-Process powershell.exe -WorkingDirectory $installRoot -ArgumentList '-NoLogo', '-NoProfile', '-Command', '.\run.cmd' | Out-Null
            Write-Host 'Runner launched in a separate console. Close that window to stop it; it does not survive sign-out.'
        }
        'foreground' {
            & '.\run.cmd'
            if ($LASTEXITCODE -ne 0) { throw "Runner exited with code $LASTEXITCODE." }
        }
        'none' { Write-Host "Setup complete. To start later, open Command Prompt in $installRoot and run: run.cmd" }
    }
    Write-Host "Runner directory: $installRoot"
    Write-Host "Diagnostic logs: $installRoot\_diag"
} catch {
    [Console]::Error.WriteLine("ERROR: $($_.Exception.Message)")
    $exitCode = 1
} finally {
    $script:ApiToken = $null
    $registrationToken = $null
    $removeToken = $null
    $configArguments = $null
    Set-Location -LiteralPath $originalLocation.Path
}
exit $exitCode
