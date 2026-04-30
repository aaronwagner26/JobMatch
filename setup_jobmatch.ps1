$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$LogDir = Join-Path $RepoRoot "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("setup-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
$RequirementsFile = Join-Path $RepoRoot "requirements.txt"
$OllamaConfigPath = Join-Path $env:USERPROFILE ".ollama\server.json"

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

function Set-OllamaLocalOnlyConfig {
    $env:OLLAMA_NO_CLOUD = "1"

    $config = @{}
    if (Test-Path $OllamaConfigPath) {
        $raw = Get-Content -Path $OllamaConfigPath -Raw -ErrorAction SilentlyContinue
        if ($raw -and $raw.Trim()) {
            try {
                $parsed = $raw | ConvertFrom-Json
                foreach ($property in $parsed.PSObject.Properties) {
                    $config[$property.Name] = $property.Value
                }
            }
            catch {
                Write-Log "Could not parse $OllamaConfigPath. Leaving it unchanged."
                return $false
            }
        }
    }

    if ($config.ContainsKey("disable_ollama_cloud") -and [bool]$config["disable_ollama_cloud"]) {
        return $false
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OllamaConfigPath) | Out-Null
    $config["disable_ollama_cloud"] = $true
    $config | ConvertTo-Json -Depth 10 | Set-Content -Path $OllamaConfigPath -Encoding UTF8
    return $true
}

Push-Location $RepoRoot

try {
    Write-Log "Setup log: $LogFile"
    Write-Log "Using Python 3.12 for setup..."
    Invoke-Python312 -Arguments @("-c", "import sys; print(sys.version); print(sys.executable)")

    $ollamaConfigChanged = Set-OllamaLocalOnlyConfig
    if ($ollamaConfigChanged) {
        Write-Log "Updated $OllamaConfigPath to disable Ollama cloud features."
        Write-Log "If Ollama is already running, quit and reopen Ollama for local-only mode to take effect."
    }

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
