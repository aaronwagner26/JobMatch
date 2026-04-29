param(
    [int]$Port = 8181
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$SetupScript = Join-Path $RepoRoot "setup_jobmatch.ps1"

Push-Location $RepoRoot

try {
    & py -3.12 -c "import nicegui" *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "JobMatch dependencies not found. Running setup..."
        & $SetupScript
    }

    Write-Host "Starting JobMatch on http://127.0.0.1:$Port/"
    & py -3.12 -m app.ui.main --port $Port
}
finally {
    Pop-Location
}
