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

Push-Location $RepoRoot

try {
    if (-not (Test-Python312Command -Arguments @("-c", "import nicegui"))) {
        Write-Host "JobMatch dependencies not found. Running setup..."
        & $SetupScript
    }

    Write-Host "Starting JobMatch on http://127.0.0.1:$Port/"
    & py -3.12 -m app.ui.main --port $Port
}
finally {
    Pop-Location
}
