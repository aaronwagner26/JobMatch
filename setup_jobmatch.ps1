$ErrorActionPreference = "Stop"

Write-Host "Using Python 3.12 for setup..."
& py -3.12 -c "import sys; print(sys.version); print(sys.executable)"

Write-Host "Upgrading packaging tools..."
& py -3.12 -m pip install --upgrade pip setuptools wheel

Write-Host "Installing JobMatch in editable mode..."
& py -3.12 -m pip install -e .

Write-Host "Installing Playwright Chromium..."
& py -3.12 -m playwright install chromium

Write-Host "Setup complete."
