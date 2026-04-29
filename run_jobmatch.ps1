param(
    [int]$Port = 8181
)

$ErrorActionPreference = "Stop"

Write-Host "Starting JobMatch on http://127.0.0.1:$Port/"
& py -3.12 -m app.ui.main --port $Port
