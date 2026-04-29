param(
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

Push-Location $RepoRoot

try {
    if (-not (Test-JobMatchDependencies)) {
        Write-Host "JobMatch dependencies not found. Running setup..."
        & $SetupScript
    }

    Write-Host "Starting JobMatch on http://127.0.0.1:$Port/"
    & py -3.12 -m app.ui.main --port $Port
}
finally {
    Pop-Location
}
