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
        & py -3.12 @Arguments 1> $stdout 2> $stderr
        return $LASTEXITCODE -eq 0
    }
    finally {
        Remove-Item $stdout, $stderr -ErrorAction SilentlyContinue
    }
}

function Test-JobMatchDependencies {
    $probe = "import importlib.util, sys; modules=['bs4','httpx','nicegui','playwright','fitz','docx','dateutil','sklearn','sentence_transformers','sqlalchemy']; missing=[name for name in modules if importlib.util.find_spec(name) is None]; sys.exit(0 if not missing else 1)"

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

function Get-PortListeners {
    param(
        [Parameter(Mandatory = $true)]
        [int]$TargetPort
    )

    return @(Get-NetTCPConnection -LocalPort $TargetPort -ErrorAction SilentlyContinue |
        Where-Object {
            $_.State -eq "Listen" -and
            $_.OwningProcess -gt 0
        })
}

function Get-ProcessCommandLine {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($process) {
        return $process.CommandLine
    }
    return $null
}

function Stop-JobMatchPortOwners {
    param(
        [Parameter(Mandatory = $true)]
        [int]$TargetPort
    )

    $listeners = @(Get-PortListeners -TargetPort $TargetPort)
    if (-not $listeners) {
        return
    }

    $owners = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($owner in $owners) {
        $commandLine = Get-ProcessCommandLine -ProcessId $owner
        if (
            $commandLine -like "*-m app.ui.main*" -or
            $commandLine -like "*app\ui\main.py*" -or
            $commandLine -like "*app/ui/main.py*"
        ) {
            Write-Host "Stopping existing JobMatch listener $owner on port $TargetPort..."
            Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
        }
    }

    Start-Sleep -Milliseconds 750
}

function Assert-PortAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [int]$TargetPort
    )

    $listeners = @(Get-PortListeners -TargetPort $TargetPort)
    if (-not $listeners) {
        return
    }

    $owners = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    if (-not $owners) {
        return
    }

    $ownerText = (
        $owners |
        ForEach-Object {
            $process = Get-Process -Id $_ -ErrorAction SilentlyContinue
            if ($process) {
                "$($process.ProcessName) (PID $_)"
            }
            else {
                "PID $_"
            }
        }
    ) -join ", "
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
    Stop-JobMatchPortOwners -TargetPort $Port
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
