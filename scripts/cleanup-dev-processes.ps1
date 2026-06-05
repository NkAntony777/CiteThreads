<#
.SYNOPSIS
    Safe cleanup of CiteThreads dev server processes.

.DESCRIPTION
    Finds processes listening on the backend (8000) and frontend (5173) ports,
    verifies they belong to THIS project (by exe path, command line, and parent
    chain), then gracefully terminates them.  Non-project processes are left
    alone and reported.

.PARAMETER ProjectRoot
    Absolute path to the CiteThreads project root (with trailing backslash).

.PARAMETER BackendPort
    Port the backend dev server listens on (default 8000).

.PARAMETER FrontendPort
    Port the frontend dev server listens on (default 5173).

.PARAMETER ResultFile
    Path to write machine-readable result markers (killed count, blocked ports).
#>
param(
    [Parameter(Mandatory)]
    [string]$ProjectRoot,

    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,

    [string]$ResultFile = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'SilentlyContinue'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Get-ListenerPids {
    param([int]$Port)
    try {
        $conns = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop
        return ($conns.OwningProcess | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
    }
    catch {
        $pids = @()
        $pattern = [regex]::Escape(":$Port") + '\s+.*\s+LISTENING\s+(\d+)\s*$'
        foreach ($line in (netstat -ano 2>$null)) {
            if ($line -match $pattern) {
                $p = [int]$Matches[1]
                if ($p -gt 0) { $pids += $p }
            }
        }
        return ($pids | Sort-Object -Unique)
    }
}

function Get-ProcessInfo {
    param([int]$Pid)
    $proc = Get-Process -Id $Pid -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId=$Pid"
    if (-not $cim) { return $null }
    return @{
        Name     = $proc.ProcessName
        ExePath  = if ($cim.ExecutablePath) { $cim.ExecutablePath } else { '' }
        CmdLine  = if ($cim.CommandLine)    { $cim.CommandLine    } else { '' }
        ParentId = $cim.ParentProcessId
    }
}

function Test-IsProjectProcess {
    <#
    Returns $true ONLY if the given PID is a CiteThreads dev-server process.
    Checks are intentionally conservative.
    #>
    param(
        [int]$Pid,
        [string]$ProjectRoot,
        [ValidateSet('backend','frontend')]
        [string]$Kind
    )

    $root = $ProjectRoot.TrimEnd('\').ToLowerInvariant()

    $info = Get-ProcessInfo $Pid
    if (-not $info) { return $false }

    $nameLow = $info.Name.ToLowerInvariant()
    $cmdLow  = $info.CmdLine.ToLowerInvariant()
    $exeLow  = $info.ExePath.ToLowerInvariant()

    # Process name must be a dev-server executable
    if ($Kind -eq 'backend') {
        if ($nameLow -notin @('python', 'pythonw')) { return $false }
    }
    else {
        if ($nameLow -notin @('node', 'nodejs'))    { return $false }
    }

    # Must have project root in exe path or command line (or parent chain)
    $hasRoot = $exeLow.Contains($root) -or $cmdLow.Contains($root)
    if (-not $hasRoot) {
        $ppid = $info.ParentId
        $found = $false
        for ($i = 0; $i -lt 5 -and $ppid -gt 0; $i++) {
            $pinfo = Get-ProcessInfo $ppid
            if (-not $pinfo) { break }
            $pExe = $pinfo.ExePath.ToLowerInvariant()
            $pCmd = $pinfo.CmdLine.ToLowerInvariant()
            if ($pExe.Contains($root) -or $pCmd.Contains($root)) {
                $found = $true; break
            }
            if ($pinfo.ParentId -eq 0 -or $pinfo.ParentId -eq $ppid) { break }
            $ppid = $pinfo.ParentId
        }
        if (-not $found) { return $false }
    }

    # Service-specific command-line check
    if ($Kind -eq 'backend') {
        if (-not ($cmdLow.Contains('uvicorn') -and
                  ($cmdLow.Contains('app.main') -or $cmdLow.Contains('app\main')))) {
            return $false
        }
    }
    else {
        $isVite  = $cmdLow.Contains('vite')
        $isNpmDev = ($cmdLow.Contains('npm') -and $cmdLow.Contains('run') -and $cmdLow.Contains('dev'))
        if (-not ($isVite -or $isNpmDev)) { return $false }
    }

    return $true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

$killedCount  = 0
$blockedPorts = @()

foreach ($entry in @(
    @{ Port = $BackendPort;  Kind = 'backend'  },
    @{ Port = $FrontendPort; Kind = 'frontend' }
)) {
    $port = $entry.Port
    $kind = $entry.Kind

    $pids = @(Get-ListenerPids $port)
    if ($pids.Count -eq 0) { continue }

    foreach ($pid in $pids) {
        if (Test-IsProjectProcess $pid $ProjectRoot $kind) {
            $info = Get-ProcessInfo $pid
            $cmdShort = if ($info.CmdLine.Length -gt 120) {
                $info.CmdLine.Substring(0, 117) + '...'
            } else {
                $info.CmdLine
            }
            Write-Host "      [kill]   port $port, PID $pid  ($kind)"
            Write-Host "               $cmdShort"

            # Graceful kill first (no /F)
            $null = taskkill /PID $pid 2>&1
            Start-Sleep -Seconds 2

            # If still alive, re-verify and force kill
            if (Get-Process -Id $pid -ErrorAction SilentlyContinue) {
                if (Test-IsProjectProcess $pid $ProjectRoot $kind) {
                    Write-Host "      [force]  port $port, PID $pid  (graceful kill timed out)"
                    $null = taskkill /F /PID $pid 2>&1
                    Start-Sleep -Milliseconds 500
                }
                else {
                    Write-Host "      [abort]  port $port, PID $pid  (process identity changed, skipping)"
                }
            }

            $killedCount++
        }
        else {
            $info = Get-ProcessInfo $pid
            $exep = if ($info.ExePath) { $info.ExePath } else { '(unknown)' }
            $cmdl = if ($info.CmdLine) { $info.CmdLine } else { '(unknown)' }
            Write-Host "      [skip]   port $port, PID $pid  (not a project process)"
            Write-Host "               path: $exep"
            if ($cmdl -ne '(unknown)') {
                $cmdShort = if ($cmdl.Length -gt 160) { $cmdl.Substring(0, 157) + '...' } else { $cmdl }
                Write-Host "               cmd:  $cmdShort"
            }
            if ($port -notin $blockedPorts) { $blockedPorts += $port }
        }
    }
}

if ($killedCount -eq 0 -and $blockedPorts.Count -eq 0) {
    Write-Host "      ports are free, nothing to clean up"
}

# Write machine-readable results to a dedicated file (avoids stream confusion)
if ($ResultFile) {
    "KILLED=$killedCount"       | Out-File -FilePath $ResultFile -Encoding ascii -Force
    "BLOCKED=$($blockedPorts -join ',')" | Out-File -FilePath $ResultFile -Encoding ascii -Append
}
