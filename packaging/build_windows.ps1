# Build InboxPilot for Windows. Run from the repository root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
python --version
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw "Tests failed; not building." }
.\.venv\Scripts\pyinstaller packaging\inboxpilot.spec --noconfirm --clean
Write-Host "Built: dist\InboxPilot\InboxPilot.exe"
if (Get-Command iscc -ErrorAction SilentlyContinue) {
    iscc packaging\installer.iss
    Write-Host "Installer: dist\InboxPilot-Setup.exe"
} else {
    Write-Host "Inno Setup (iscc) not found; skipping installer. Zip dist\InboxPilot to distribute."
}
