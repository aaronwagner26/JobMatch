param(
    [string]$BindHost = "",
    [int]$Port = 8181
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$SetupScript = Join-Path $RepoRoot "setup_jobmatch.ps1"

function Test-Python312Command {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $stdout = [System.IO.Path]::GetTempFileName()
    $stderr = [System.IO.Path]::GetTempFileName()

    try {
        $process = Start-Process -FilePath "py" `
            -ArgumentList (@("-3.12") + $Arguments) `
            -NoNewWindow `
            -PassThru `
            -Wait `
            -RedirectStandardOutput $stdout `
            -RedirectStandardError $stderr
        return $process.ExitCode -eq 0
    }
    finally {
        Remove-Item $stdout, $stderr -ErrorAction SilentlyContinue
    }
}

function Test-JobMatchDependencies {
    $probe = @"
import importlib.util
import sys

modules = [
    'bs4',
    'httpx',
    'nicegui',
    'playwright',
    'fitz',
    'docx',
    'dateutil',
    'sklearn',
    'sentence_transformers',
    'sqlalchemy',
]

missing = [name for name in modules if importlib.util.find_spec(name) is None]
sys.exit(0 if not missing else 1)
"@

    return Test-Python312Command -Arguments @("-c", $probe)
}

function Get-PrimaryIPv4 {
    $addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.PrefixOrigin -ne "WellKnown"
        } |
        Sort-Object SkipAsSource, InterfaceMetric

    if ($addresses) {
        return $addresses[0].IPAddress
    }
    return $null
}

function Get-JobMatchPythonProcesses {
    return Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -like "*-m app.ui.main*"
        }
}

function Stop-JobMatchPythonProcesses {
    $processes = @(Get-JobMatchPythonProcesses)
    if (-not $processes) {
        return
    }

    foreach ($process in $processes) {
        Write-Host "Stopping previous JobMatch process $($process.ProcessId)..."
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Milliseconds 750
}

function Assert-PortAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [int]$TargetPort
    )

    $connections = @(Get-NetTCPConnection -LocalPort $TargetPort -ErrorAction SilentlyContinue)
    if (-not $connections) {
        return
    }

    $owners = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $owners) {
        return
    }

    $ownerText = ($owners | ForEach-Object { "PID $_" }) -join ", "
    throw "Port $TargetPort is still in use by $ownerText. Stop that process or choose a different port."
}

Push-Location $RepoRoot

try {
    if (-not (Test-JobMatchDependencies)) {
        Write-Host "JobMatch dependencies not found. Running setup..."
        & $SetupScript
    }

    $ResolvedBindHost = $BindHost
    if (-not $ResolvedBindHost) {
        $ResolvedBindHost = Get-PrimaryIPv4
        if (-not $ResolvedBindHost) {
            $ResolvedBindHost = "127.0.0.1"
        }
    }

    Stop-JobMatchPythonProcesses
    Assert-PortAvailable -TargetPort $Port

    if ($ResolvedBindHost -eq "127.0.0.1") {
        Write-Host "Starting JobMatch on http://127.0.0.1:$Port/"
    }
    else {
        Write-Host "Starting JobMatch on http://$ResolvedBindHost`:$Port/"
    }

    & py -3.12 -m app.ui.main --host $ResolvedBindHost --port $Port
}
finally {
    Pop-Location
}
