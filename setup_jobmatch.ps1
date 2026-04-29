$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$LogDir = Join-Path $RepoRoot "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("setup-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$RequirementsFile = Join-Path $RepoRoot "requirements.txt"

function Write-Log {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $line = "[{0}] {1}" -f (Get-Date -Format "h:mm:ss tt"), $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

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

function Invoke-Python312 {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    Write-Log ("Running: py -3.12 {0}" -f ($Arguments -join " "))
    & py -3.12 @Arguments 2>&1 | Tee-Object -FilePath $LogFile -Append
    $exitCode = $LASTEXITCODE
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: py -3.12 $($Arguments -join ' ')"
    }
    return $exitCode
}

Push-Location $RepoRoot

try {
    Write-Log "Setup log: $LogFile"
    Write-Log "Using Python 3.12 for setup..."
    Invoke-Python312 -Arguments @("-c", "import sys; print(sys.version); print(sys.executable)")

    Write-Log "Checking pip availability..."
    if (-not (Test-Python312Command -Arguments @("-m", "pip", "--version"))) {
        Write-Log "pip not found, bootstrapping with ensurepip..."
        Invoke-Python312 -Arguments @("-m", "ensurepip", "--upgrade")
    }

    Write-Log "Installing JobMatch Python dependencies..."
    Write-Log "First-time dependency install can take several minutes because sentence-transformers pulls torch."
    Invoke-Python312 -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "--verbose", "-r", $RequirementsFile)

    Write-Log "Installing Playwright Chromium..."
    Invoke-Python312 -Arguments @("-m", "playwright", "install", "chromium")

    Write-Log "Setup complete."
}
finally {
    Pop-Location
}
