param(
    [string]$Host = "0.0.0.0",
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

Push-Location $RepoRoot

try {
    if (-not (Test-JobMatchDependencies)) {
        Write-Host "JobMatch dependencies not found. Running setup..."
        & $SetupScript
    }

    if ($Host -eq "0.0.0.0") {
        $lanIp = Get-PrimaryIPv4
        if ($lanIp) {
            Write-Host "Starting JobMatch on:"
            Write-Host "  Local: http://127.0.0.1:$Port/"
            Write-Host "  LAN:   http://$lanIp`:$Port/"
        }
        else {
            Write-Host "Starting JobMatch on http://127.0.0.1:$Port/"
        }
    }
    else {
        Write-Host "Starting JobMatch on http://$Host`:$Port/"
    }

    & py -3.12 -m app.ui.main --host $Host --port $Port
}
finally {
    Pop-Location
}
