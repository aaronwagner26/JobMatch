$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot

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

Push-Location $RepoRoot

try {
    Write-Host "Using Python 3.12 for setup..."
    & py -3.12 -c "import sys; print(sys.version); print(sys.executable)"

    Write-Host "Checking pip availability..."
    if (-not (Test-Python312Command -Arguments @("-m", "pip", "--version"))) {
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
