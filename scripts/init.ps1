param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'install', 'start', 'start-backend', 'start-frontend', 'check', 'test')]
    [string]$Command = 'help',
    [switch]$NoInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}

function Invoke-InDir {
    param(
        [string]$Dir,
        [string]$Exe,
        [string[]]$Args
    )

    Push-Location -LiteralPath $Dir
    try {
        & $Exe @Args
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed (exit $LASTEXITCODE): $Exe $($Args -join ' ')"
        }
    }
    finally {
        Pop-Location
    }
}

function Ensure-BackendVenv {
    $root = Get-RepoRoot
    $backend = Join-Path $root 'backend'
    $venv = Join-Path $backend '.venv'
    $python = Join-Path (Join-Path $venv 'Scripts') 'python.exe'

    if (-not (Test-Path -LiteralPath $python)) {
        Write-Host 'Creating backend venv...'
        Invoke-InDir -Dir $backend -Exe 'python' -Args @('-m', 'venv', '.venv')
    }

    return $python
}

function Install-Backend {
    $root = Get-RepoRoot
    $backend = Join-Path $root 'backend'
    $python = Ensure-BackendVenv
    Write-Host 'Installing backend requirements...'
    Invoke-InDir -Dir $backend -Exe $python -Args @('-m', 'pip', 'install', '-r', 'requirements.txt')
}

function Install-Frontend {
    $root = Get-RepoRoot
    $frontend = Join-Path $root 'frontend'
    Write-Host 'Installing frontend dependencies (npm install)...'
    Invoke-InDir -Dir $frontend -Exe 'npm' -Args @('install')
}

function Start-Backend {
    $root = Get-RepoRoot
    $backend = Join-Path $root 'backend'
    $python = Ensure-BackendVenv

    $cmd = "& `"$python`" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
    Start-Process -FilePath 'powershell' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $cmd) -WorkingDirectory $backend | Out-Null
    Write-Host 'Backend started: http://localhost:8000 (docs: /docs)'
}

function Start-Frontend {
    $root = Get-RepoRoot
    $frontend = Join-Path $root 'frontend'
    $cmd = 'npm run dev'
    Start-Process -FilePath 'powershell' -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $cmd) -WorkingDirectory $frontend | Out-Null
    Write-Host 'Frontend started: http://localhost:5173'
}

function Check-All {
    $root = Get-RepoRoot
    $frontend = Join-Path $root 'frontend'
    $backend = Join-Path $root 'backend'
    $python = Ensure-BackendVenv

    Write-Host 'Frontend typecheck...'
    Invoke-InDir -Dir $frontend -Exe 'npx' -Args @('tsc', '--noEmit')
    Write-Host 'Frontend lint...'
    Invoke-InDir -Dir $frontend -Exe 'npm' -Args @('run', 'lint')

    Write-Host 'Backend tests...'
    Invoke-InDir -Dir $backend -Exe $python -Args @('-m', 'pytest')
}

function Test-All {
    $root = Get-RepoRoot
    $frontend = Join-Path $root 'frontend'
    $backend = Join-Path $root 'backend'
    $python = Ensure-BackendVenv

    Write-Host 'Backend tests...'
    Invoke-InDir -Dir $backend -Exe $python -Args @('-m', 'pytest')
    Write-Host 'Frontend tests...'
    Invoke-InDir -Dir $frontend -Exe 'npm' -Args @('run', 'test:run')
}

switch ($Command) {
    'help' {
        Write-Host 'scripts/init.ps1 commands:'
        Write-Host '  install         Create venv, install backend reqs, npm install'
        Write-Host '  start           Start backend + frontend in separate shells'
        Write-Host '  start-backend   Start backend dev server'
        Write-Host '  start-frontend  Start frontend dev server'
        Write-Host '  check           Frontend typecheck+lint, backend pytest'
        Write-Host '  test            Backend pytest, frontend test:run'
        Write-Host ''
        Write-Host 'Options:'
        Write-Host '  -NoInstall      Skip installing dependencies for start'
    }
    'install' {
        Install-Backend
        Install-Frontend
    }
    'start' {
        if (-not $NoInstall) {
            Install-Backend
            Install-Frontend
        }
        Start-Backend
        Start-Frontend
    }
    'start-backend' {
        if (-not $NoInstall) { Install-Backend }
        Start-Backend
    }
    'start-frontend' {
        if (-not $NoInstall) { Install-Frontend }
        Start-Frontend
    }
    'check' {
        Check-All
    }
    'test' {
        Test-All
    }
}
