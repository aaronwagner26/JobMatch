$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot

Push-Location $RepoRoot

try {
    Write-Host "Using Python 3.12 for setup..."
    & py -3.12 -c "import sys; print(sys.version); print(sys.executable)"

    Write-Host "Checking pip availability..."
    & py -3.12 -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip not found, bootstrapping with ensurepip..."
        & py -3.12 -m ensurepip --upgrade
    }

    Write-Host "Installing JobMatch in editable mode..."
    & py -3.12 -m pip install --disable-pip-version-check -e .

    Write-Host "Installing Playwright Chromium..."
    & py -3.12 -m playwright install chromium

    Write-Host "Setup complete."
}
finally {
    Pop-Location
}
