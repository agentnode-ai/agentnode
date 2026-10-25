# AgentNode local worker -- Windows installer (WSL2 only; local-worker slice 2, Codex Q-LW2-0001 D7/D9 as amended).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File install-agentnode-worker.ps1 [-Name NAME] [-Concurrency 1] [-Queue 2]
#   ... -Check        only the prerequisite checks, nothing changed
#   ... -Uninstall [-Revoke]
#
# Nothing ever runs on Windows itself: the agent runs inside a dedicated WSL2 distribution "AgentNode", made from a
# DATED official Rocky Linux 10 WSL image, with rootless podman. BEFORE anything is imported, extracted or executed,
# this script verifies -- here, on Windows, with the Ed25519 verifier embedded below -- the release manifest's
# signature, its expiry, this platform, the per-user release floor, and the size and SHA-256 of every file it will use.
# No Authenticode signature is claimed: the script's own SHA-256 is what the authenticated pairing page shows.
# Refusals (named, before any payload):
#   release_verifier_unavailable release_signature_invalid release_manifest_invalid release_expired release_downgrade
#   release_platform_mismatch release_floor_unreadable artifact_hash_mismatch wsl_virtualization_disabled
#   wsl_feature_disabled wsl_absent wsl1_unsupported wsl_too_old wsl_distribution_conflict wsl_systemd_unavailable
#   wsl_acl_unsafe
param([string]$Name = $env:COMPUTERNAME, [int]$Concurrency = 1, [int]$Queue = 2, [switch]$Check, [switch]$Uninstall,
      [switch]$Revoke, [string]$Bundle = $PSScriptRoot)
$ErrorActionPreference = 'Stop'
$Distro = 'AgentNode'
$Home_ = Join-Path $env:LOCALAPPDATA 'AgentNode'
$Floor = Join-Path $Home_ 'release-floor.json'
$TaskName = 'AgentNode Worker'
$ReleasePubHex = '@RELEASE_PUB_HEX@'
$WslMin = [version]'2.0.0'
function Refuse([string]$code, [string]$detail = '') { Write-Output ("REFUSED {0}{1}" -f $code, $(if ($detail) { ": $detail" } else { '' })); exit 2 }
function Say([string]$t) { Write-Output "  $t" }

# ---- the vendored Ed25519 verifier (sdk/deploy/local-worker/windows/Ed25519.ps1, embedded at release build) ----------
# @ED25519_VERIFIER@
if (-not (Get-Command Test-Ed25519Signature -ErrorAction SilentlyContinue)) { Refuse release_verifier_unavailable 'the embedded verifier is missing' }

function HexBytes([string]$h) { $b = New-Object byte[] ($h.Length / 2); for ($i = 0; $i -lt $b.Length; $i++) { $b[$i] = [Convert]::ToByte($h.Substring($i * 2, 2), 16) }; return , $b }
function Wsl([string[]]$a) {
    # wsl.exe writes UTF-16 to a pipe; read it as such
    $psi = New-Object Diagnostics.ProcessStartInfo 'wsl.exe'
    $psi.Arguments = ($a | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join ' '
    $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::Unicode; $psi.CreateNoWindow = $true
    $p = [Diagnostics.Process]::Start($psi); $o = $p.StandardOutput.ReadToEnd(); $e = $p.StandardError.ReadToEnd(); $p.WaitForExit()
    return [pscustomobject]@{ Code = $p.ExitCode; Out = ($o -replace "`0", ''); Err = $e }
}
function InDistro([string]$cmd) {   # a command inside the AgentNode distribution, as root, output as UTF-8 text
    $psi = New-Object Diagnostics.ProcessStartInfo 'wsl.exe'
    $psi.Arguments = "-d $Distro -u root -- /bin/sh -c `"$($cmd -replace '"', '\"')`""
    $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $p = [Diagnostics.Process]::Start($psi); $o = $p.StandardOutput.ReadToEnd(); $e = $p.StandardError.ReadToEnd(); $p.WaitForExit()
    return [pscustomobject]@{ Code = $p.ExitCode; Out = $o; Err = $e }
}
function AclIsPrivate([string]$path) {
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $ok = @($me, 'S-1-5-18', 'S-1-5-32-544')                     # this user, SYSTEM, Administrators
    $acl = Get-Acl -LiteralPath $path
    if (-not $acl.AreAccessRulesProtected) { return $false }
    foreach ($r in $acl.Access) {
        $sid = $r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($ok -notcontains $sid -and $r.AccessControlType -eq 'Allow') { return $false }
    }
    return $true
}
function MakePrivate([string]$path) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
    foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18', 'S-1-5-32-544')) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier $sid),
            'FullControl', $inherit, 'None', 'Allow')))
    }
    Set-Acl -LiteralPath $path -AclObject $acl
}

# ---- uninstall ------------------------------------------------------------------------------------------------------
if ($Uninstall) {
    $l = Wsl @('-l', '-q')
    if (($l.Out -split "`r?`n") -contains $Distro) {
        $args_ = if ($Revoke) { '--revoke' } else { '' }
        $r = InDistro "sh /opt/agentnode-agent-bundle/uninstall.sh $args_"
        Say ($r.Out.Trim() -replace "`n", ' | ')
        Wsl @('--terminate', $Distro) | Out-Null
        $u = Wsl @('--unregister', $Distro); Say "distribution removed: exit $($u.Code)"
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Home_) { Remove-Item -LiteralPath $Home_ -Recurse -Force }
    $left = @()
    if ((Wsl @('-l', '-q')).Out -split "`r?`n" -contains $Distro) { $left += 'distribution' }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { $left += 'task' }
    if (Test-Path -LiteralPath $Home_) { $left += $Home_ }
    if ($left) { Write-Output "  NOT completely removed: $($left -join ', ')"; exit 1 }
    Say 'removed completely: no distribution, no task, no files.'
    exit 0
}

# ---- 1. the signature, the manifest, every file: BEFORE anything is imported or run --------------------------------
$mPath = Join-Path $Bundle 'manifest.json'; $sPath = Join-Path $Bundle 'manifest.sig'
if (-not (Test-Path -LiteralPath $mPath) -or -not (Test-Path -LiteralPath $sPath)) { Refuse release_manifest_invalid 'no manifest/signature beside this script' }
$mBytes = [IO.File]::ReadAllBytes($mPath); $sig = [IO.File]::ReadAllBytes($sPath)
$pre = [Text.Encoding]::ASCII.GetBytes("agentnode-worker-release/1`n")
$signed = New-Object byte[] ($pre.Length + $mBytes.Length); [Array]::Copy($pre, $signed, $pre.Length); [Array]::Copy($mBytes, 0, $signed, $pre.Length, $mBytes.Length)
if (-not (Test-Ed25519Signature -PublicKey (HexBytes $ReleasePubHex) -Message $signed -Signature $sig)) { Refuse release_signature_invalid }
Say 'release signature verified'
foreach ($b in $mBytes) { if ($b -gt 127) { Refuse release_manifest_invalid 'not ASCII' } }
try { $m = [Text.Encoding]::ASCII.GetString($mBytes) | ConvertFrom-Json } catch { Refuse release_manifest_invalid 'not JSON' }
$keys = @('schema', 'channel', 'release_seq', 'min_release_seq', 'version', 'git_commit', 'issued_at', 'expires_at', 'compat', 'pins', 'artifacts')
if ($m.schema -ne 'agentnode-worker-release/1' -or (Compare-Object $keys @($m.PSObject.Properties.Name))) { Refuse release_manifest_invalid 'shape' }
$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
if (($m.expires_at - $m.issued_at) -le 0 -or ($m.expires_at - $m.issued_at) -gt 30 * 86400) { Refuse release_manifest_invalid 'lifetime' }
if ($now -ge $m.expires_at -or $now -lt ($m.issued_at - 300)) { Refuse release_expired }
$floorSeq = 0
if (Test-Path -LiteralPath $Floor) {
    if (-not (AclIsPrivate $Home_)) { Refuse release_floor_unreadable 'the floor directory is not private to this user' }
    try { $floorSeq = [int]((Get-Content -LiteralPath $Floor -Raw | ConvertFrom-Json).release_seq) } catch { Refuse release_floor_unreadable }
}
if ($m.release_seq -lt $floorSeq) { Refuse release_downgrade "$($m.release_seq) is below the installed floor $floorSeq" }
$mine = @($m.artifacts | Where-Object { @('windows-x86_64', 'wsl-rocky10-x86_64', 'linux-x86_64', 'any') -contains $_.platform })
foreach ($p in @('windows-x86_64', 'wsl-rocky10-x86_64', 'linux-x86_64')) {
    if (-not ($m.artifacts | Where-Object { $_.platform -eq $p })) { Refuse release_platform_mismatch "no $p artifact" }
}
foreach ($a in $mine) {
    $f = Join-Path $Bundle $a.name
    if ($a.name -match '[\\/]' -or -not (Test-Path -LiteralPath $f -PathType Leaf)) { Refuse artifact_hash_mismatch $a.name }
    if ((Get-Item -LiteralPath $f).Length -ne $a.size) { Refuse artifact_hash_mismatch $a.name }
    if ((Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower() -ne $a.sha256) { Refuse artifact_hash_mismatch $a.name }
}
$rootfs = @($m.artifacts | Where-Object { $_.kind -eq 'rootfs' -and $_.platform -eq 'wsl-rocky10-x86_64' })
if ($rootfs.Count -ne 1) { Refuse release_manifest_invalid 'exactly one rootfs' }
Say ("manifest: release {0} ({1}), {2} files verified" -f $m.release_seq, $m.version, $mine.Count)

# ---- 2. this machine, read-only, BEFORE anything is changed ---------------------------------------------------------
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cs = Get-CimInstance Win32_ComputerSystem
if (-not $cs.HypervisorPresent -and -not $cpu.VirtualizationFirmwareEnabled) { Refuse wsl_virtualization_disabled 'virtualization is switched off in the firmware (BIOS/UEFI)' }
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { Refuse wsl_absent 'wsl.exe is not installed' }
foreach ($feat in @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')) {
    $fs = Get-CimInstance Win32_OptionalFeature -Filter "Name='$feat'" -ErrorAction SilentlyContinue
    if ($null -eq $fs -or $fs.InstallState -ne 1) { Refuse wsl_feature_disabled "$feat is not enabled" }
}
if (-not $cs.HypervisorPresent) { Refuse wsl_feature_disabled 'no hypervisor is running (reboot after enabling the features)' }
$v = Wsl @('--version')
if ($v.Code -ne 0 -or $v.Out -notmatch '(\d+\.\d+\.\d+)') { Refuse wsl_too_old 'this WSL has no --version (install the current WSL)' }
if ([version]$Matches[1] -lt $WslMin) { Refuse wsl_too_old "WSL $($Matches[1]) < $WslMin" }
$list = (Wsl @('-l', '-q')).Out -split "`r?`n" | Where-Object { $_ }
if ($list -contains $Distro) { Refuse wsl_distribution_conflict "a distribution named $Distro already exists" }
if ($Check) { Say 'all prerequisites hold; nothing was changed (-Check)'; exit 0 }

# ---- 3. the private directory, the distribution from the verified image --------------------------------------------
New-Item -ItemType Directory -Force -Path $Home_ | Out-Null
MakePrivate $Home_
if (-not (AclIsPrivate $Home_)) { Refuse wsl_acl_unsafe "$Home_ is not private to this user" }
$imp = Wsl @('--import', $Distro, (Join-Path $Home_ 'wsl'), (Join-Path $Bundle $rootfs[0].name), '--version', '2')
if ($imp.Code -ne 0) { Refuse wsl1_unsupported ("the WSL2 import failed: " + ($imp.Out + $imp.Err).Trim()) }
$vv = (Wsl @('-l', '-v')).Out
if ($vv -notmatch "$Distro\s+\S+\s+2") { Wsl @('--unregister', $Distro) | Out-Null; Refuse wsl1_unsupported 'the distribution is not WSL version 2' }
Say "distribution $Distro imported from $($rootfs[0].name) (SHA-256 checked above)"
$mnt = '/mnt/' + $Bundle.Substring(0, 1).ToLower() + ($Bundle.Substring(2) -replace '\\', '/')
$r = InDistro "printf '[boot]\nsystemd=true\n[user]\ndefault=root\n[interop]\nappendWindowsPath=false\n' > /etc/wsl.conf"
Wsl @('--terminate', $Distro) | Out-Null
$r = InDistro 'for i in 1 2 3 4 5 6 7 8 9 10; do s=$(systemctl is-system-running 2>/dev/null); case $s in running|degraded) echo $s; exit 0;; esac; sleep 2; done; echo none'
if ($r.Out.Trim() -notin @('running', 'degraded')) { Refuse wsl_systemd_unavailable "systemd did not start in $Distro ($($r.Out.Trim()))" }
Say "systemd is $($r.Out.Trim()) in $Distro"
# the bundle into the distribution's own filesystem, then every RPM checked with rpm -K against the pinned Rocky key
$r = InDistro "rm -rf /opt/agentnode-agent-bundle && mkdir -p /opt/agentnode-agent-bundle && cp '$mnt'/* /opt/agentnode-agent-bundle/ && chmod 0755 /opt/agentnode-agent-bundle && echo copied"
if ($r.Out.Trim() -ne 'copied') { Refuse artifact_hash_mismatch "the bundle could not be copied into $Distro" }
# the SHA-256 of the very manifest bytes verified above: inside, nothing copied that differs from them is believed
$mSha = -join ([Security.Cryptography.SHA256]::Create().ComputeHash($mBytes) | ForEach-Object { $_.ToString('x2') })
$r = InDistro "sh /opt/agentnode-agent-bundle/wsl-inner.sh $mSha"
Write-Output ($r.Out.TrimEnd())
if ($r.Code -ne 0) { Refuse wsl_systemd_unavailable "the distribution could not be prepared ($($r.Code))" }

# ---- 4. the Linux installer inside the distribution (it verifies the manifest again with OpenSSL); pairing ------------
Say 'starting the Linux installer inside the distribution; pair this machine in your browser when the code appears'
$psi = New-Object Diagnostics.ProcessStartInfo 'wsl.exe'
$psi.Arguments = "-d $Distro -u root -- /bin/sh /opt/agentnode-agent-bundle/install.sh --name `"$Name`" --concurrency $Concurrency --queue $Queue"
$psi.UseShellExecute = $false
$p = [Diagnostics.Process]::Start($psi); $p.WaitForExit()
if ($p.ExitCode -ne 0) { Write-Output "  the Linux installer stopped with $($p.ExitCode); see its output above"; exit $p.ExitCode }

# ---- 5. keep the distribution running while this user is logged on (no stored password, no Windows-side secret) ---
$action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument "-d $Distro --exec /bin/sleep infinity"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
$floorDoc = @{ release_seq = [int]$m.release_seq; at = $now } | ConvertTo-Json -Compress
[IO.File]::WriteAllText($Floor, $floorDoc)
Say "installed. The agent runs inside WSL2 ($Distro) while you are logged on; nothing listens on this PC."
Say "Remove everything with: powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Uninstall [-Revoke]"
