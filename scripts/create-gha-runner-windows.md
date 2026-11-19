# Create a GitHub Actions runner on Windows

Download `create-gha-runner.ps1` and run it from PowerShell. For a Windows service,
use a terminal with administrator privileges:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\create-gha-runner.ps1
```

The script uses built-in Windows PowerShell 5.1 for HTTPS requests,
JSON, SHA256 verification, ZIP extraction, and hidden token input. It prompts for
`ORG_ADMIN_TOKEN` if the environment variable is unset. Use a GitHub token with
permission to manage runners for the chosen organization or repository, just as
with `create-gha-runner.sh`. The admin token is removed from the setup process's
environment before invoking installers or runner processes. An environment
variable already set in the parent terminal remains in that terminal when using
the separate `powershell.exe` process shown above.

The prompts support organization or repository scope, runner name, labels, work
folder, ephemeral registration, replacement, installation directory, runner
group, and recreating an existing runner. Existing installations default to
`skip`. Recreation removes the existing registration and service; stop any runner
console first and wait for active jobs to finish. Use the same scope and
organization/repository as the existing registration when recreating it.

Windows-specific defaults and options:

- The install directory defaults to `C:\actions-runner` (or `actions-runner` at the
  root of the Windows system drive). This follows GitHub's recommendation to
  avoid service identity permissions and long paths under user profile folders.
  Choose a different folder at the prompt for additional runners on the same host.
- `RUNNER_PLATFORM` must be unset or `win`. Architecture detection supports x64,
  ARM64, and x86; `RUNNER_ARCH` overrides it. The selected architecture must have
  a Windows ZIP asset in the latest `actions/runner` release.
- Labels default to `self-hosted,Windows,<architecture>`; organization runners
  default to group `Default`, with `RUNNER_GROUP` available as an override.
- `auto` installs and starts a Windows service when the terminal is elevated;
  otherwise it launches a separate runner console (`background`).
- `service` requires **Run as administrator** and uses GitHub's default Windows
  service account, `NT AUTHORITY\NETWORK SERVICE`. Provide credentials and tools
  for that account when workflows need them.
- `background` opens a separate console running `run.cmd`. It lasts until the
  window closes or the user signs out, and does not start again after reboot.
- `foreground` runs `run.cmd` in the current terminal; `none` registers the runner
  without starting it. Runner diagnostics are in the installation's `_diag` folder.

Service startup is checked for up to two minutes. If startup cannot be confirmed,
setup returns an error with the observed service state, available Windows exit
codes, log location, and diagnostic commands. Registration remains intact. A
service reaching `Running` confirms its Windows state; use GitHub's runner status
or the listener log to confirm that it is connected and ready for jobs.
Diagnostics are also shown when GitHub's configuration command installs the
service but fails while starting it. Changing to PowerShell alone does not prove
that an existing service startup problem is resolved; inspect the reported events
and logs if startup still fails.

For an existing installation, run these read-only commands in PowerShell from
the runner directory (they can also be tried in an SSH session):

```powershell
$serviceName = (Get-Content -LiteralPath .service -Raw).Trim()
Get-Service -Name $serviceName | Format-List Name,Status
Get-CimInstance -ClassName Win32_Service | Where-Object Name -eq $serviceName |
    Format-List State,ExitCode,ServiceSpecificExitCode,StartName,PathName
Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='ActionsRunnerService'} -MaxEvents 10 |
    Format-List TimeCreated,LevelDisplayName,Message
```

As in the Bash script, AWS CLI is a prerequisite. If missing, the script offers
to install AWS CLI v2 using AWS's signed Windows MSI, with a Windows elevation
prompt. The MSI is x64; ARM64 hosts require Windows x64 emulation. On hosts that
cannot run that package, install a compatible AWS CLI beforehand. No AWS login
or AWS resources are created by setup.

Runner downloads are checked against GitHub's release-asset SHA256 digest when
available; otherwise ZIP extraction validates the archive format. GitHub's
`config.cmd` delegates to `bin\Runner.Listener.exe`; setup invokes that executable
directly with argument arrays to avoid batch metacharacter expansion in prompted
values. `run.cmd` remains responsible for the runner execution/update loop.

References: [GitHub runner services](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application?platform=windows)
and [AWS CLI Windows installation](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
