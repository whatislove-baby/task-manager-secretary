param([string]$Output = 'handover/windows-docker-skill/environment')

# CI infrastructure only. The product continues to call its real native Docker CLI.
# Official supported Windows Server 2025 / WSL2 / Moby distribution:
# https://docs.rancherdesktop.io/getting-started/installation/
# https://docs.rancherdesktop.io/references/rdctl-command-reference/
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or $env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
  throw 'This setup is restricted to disposable GitHub-hosted Windows CI. It must not run on a user computer.'
}
$os = Get-CimInstance Win32_OperatingSystem
if ($os.Caption -notmatch 'Windows Server 2025') { throw 'This CI setup was validated only for the Windows Server 2025 runner.' }
$Output = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force $Output | Out-Null
$version = '1.25.0'
$sha256 = '8e8a703cafc101495bfc9aecc515a91f9c781e572c39d93698bfa96655ec21f8'
$url = "https://github.com/rancher-sandbox/rancher-desktop/releases/download/v$version/Rancher.Desktop.Setup.$version.msi"
$result = [ordered]@{passed=$false; provider='Rancher Desktop'; version=$version; engine='Moby'; windowsHost=$os.Caption; imageVersion=$env:ImageVersion; installerUrl=$url; expectedInstallerSha256=$sha256; dockerDesktopVerified=$false; reason='Setup did not finish'; commands=@()}

function Invoke-Native([string]$Name, [string]$File, [string[]]$Arguments, [int]$Seconds = 30) {
  # rdctl deliberately passes its stdout/stderr to the persistent Desktop app.
  # Redirect directly to files; reading a pipe to EOF would wait for that app to
  # quit even after rdctl --wait has reported a ready engine and exited.
  $stdoutPath = Join-Path $Output "$Name.stdout.log"
  $stderrPath = Join-Path $Output "$Name.stderr.log"
  $quoted = foreach ($argument in $Arguments) {
    $escaped = [regex]::Replace($argument, '(\\*)"', '$1$1\"')
    '"' + [regex]::Replace($escaped, '(\\+)$', '$1$1') + '"'
  }
  $process = $null
  $started = [DateTime]::UtcNow
  try {
    $process = Start-Process -FilePath $File -ArgumentList ($quoted -join ' ') -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $timedOut = -not $process.WaitForExit($Seconds * 1000)
    if ($timedOut) {
      $process.Kill($true)
      if (-not $process.WaitForExit(5000)) { throw "$Name could not be stopped" }
    }
    $out = [string](Get-Content -Raw -Path $stdoutPath -ErrorAction SilentlyContinue)
    $err = [string](Get-Content -Raw -Path $stderrPath -ErrorAction SilentlyContinue)
    $out = $out.Replace([string][char]0, '')
    $err = $err.Replace([string][char]0, '')
    $record = @{name=$Name; exitCode=$process.ExitCode; timedOut=$timedOut; elapsedSeconds=[Math]::Round(([DateTime]::UtcNow-$started).TotalSeconds, 3)}
    $result.commands += $record
    @("command: $File $($Arguments -join ' ')", "exit: $($record.exitCode); timeout: $timedOut", 'stdout:', $out, 'stderr:', $err) | Set-Content -Encoding utf8 (Join-Path $Output "$Name.log")
    Write-Host "$Name exit=$($record.exitCode) timeout=$timedOut"
    if ($timedOut -or $process.ExitCode -ne 0) { throw "$Name failed; see $Name.log. $err" }
    return $out.Trim()
  } finally { if ($process) { $process.Dispose() } }
}

try {
  $null = Invoke-Native 'wsl-version' 'wsl.exe' @('--version')
  $null = Invoke-Native 'wsl-status' 'wsl.exe' @('--status')
  $null = Invoke-Native 'wsl-default-two' 'wsl.exe' @('--set-default-version', '2')
  $installer = Join-Path $env:RUNNER_TEMP "Rancher.Desktop.Setup.$version.msi"
  Invoke-WebRequest -Uri $url -OutFile $installer -TimeoutSec 240
  $actual = (Get-FileHash -Algorithm SHA256 $installer).Hash.ToLowerInvariant()
  $result.actualInstallerSha256 = $actual
  if ($actual -ne $sha256) { throw 'Official Rancher Desktop MSI did not match its pinned SHA-256.' }
  $null = Invoke-Native 'install' 'msiexec.exe' @('/i', $installer, '/qn', '/norestart', 'ALLUSERS=1', '/L*v', (Join-Path $Output 'installer.log')) 300
  $bin = Join-Path $env:ProgramFiles 'Rancher Desktop\resources\resources\win32\bin'
  $rdctl = Join-Path $bin 'rdctl.exe'
  $docker = Join-Path $bin 'docker.exe'
  if (-not (Test-Path $rdctl -PathType Leaf) -or -not (Test-Path $docker -PathType Leaf)) { throw "Official native CLI files were not installed in $bin" }
  $result.dockerExecutable = $docker
  $result.rdctlExecutable = $rdctl
  $env:PATH = "$bin;$env:PATH"
  $bin | Out-File -FilePath $env:GITHUB_PATH -Encoding utf8 -Append
  # The hosted image's Windows-container daemon owns docker_engine. Stop this
  # disposable CI service so Rancher's local Linux daemon can own the same pipe.
  $service = Get-Service -Name docker -ErrorAction SilentlyContinue
  if ($service -and $service.Status -ne 'Stopped') {
    Stop-Service -Name docker -ErrorAction Stop
    $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    $result.stoppedPreinstalledWindowsDocker = $true
  }
  $null = Invoke-Native 'rdctl-version' $rdctl @('version')
  $null = Invoke-Native 'rancher-start' $rdctl @('start', '--container-engine.name=moby', '--kubernetes.enabled=false', '--application.start-in-background=true', '--application.telemetry.enabled=false', '--application.updater.enabled=false', '--application.auto-start=false', '--application.admin-access=false', '--no-modal-dialogs', '--wait', '--wait-timeout=7m') 450
  # Default is Docker's built-in local Windows named-pipe context. No remote host,
  # custom proxy, WSL client wrapper, or alternate production execution path.
  $null = Invoke-Native 'docker-context-select' $docker @('context', 'use', 'default')
  $endpoint = Invoke-Native 'docker-context' $docker @('context', 'inspect', '--format', '{{.Endpoints.docker.Host}}')
  if ($endpoint -ne 'npipe:////./pipe/docker_engine') { throw "Unexpected Docker endpoint: $endpoint" }
  $result.endpoint = $endpoint
  $versionJson = Invoke-Native 'docker-version' $docker @('--host', $endpoint, 'version', '--format', '{{json .}}')
  $infoJson = Invoke-Native 'docker-info' $docker @('--host', $endpoint, 'info', '--format', '{{json .}}')
  $engine = $infoJson | ConvertFrom-Json
  if ($engine.OSType -ne 'linux') { throw 'The native Windows Docker client did not reach an actual Linux engine.' }
  $result.serverVersion = $engine.ServerVersion
  $result.operatingSystem = $engine.OperatingSystem
  $result.kernelVersion = $engine.KernelVersion
  $null = Invoke-Native 'wsl-after' 'wsl.exe' @('--list', '--verbose')
  $result.passed = $true
  $result.reason = 'Official Rancher Desktop Moby Linux engine is reachable through the real Windows Docker CLI and local named pipe. Skill tests run separately.'
} catch {
  $result.reason = $_.Exception.Message
  Write-Host "WINDOWS_DOCKER_SETUP_FAILED: $($result.reason)"
} finally {
  # No settings or rd-engine.json are copied: those can contain local API credentials.
  $result | ConvertTo-Json -Depth 8 | Set-Content -Encoding utf8 (Join-Path $Output 'environment.json')
  Write-Host ($result | ConvertTo-Json -Depth 8 -Compress)
}
if (-not $result.passed) { throw $result.reason }
