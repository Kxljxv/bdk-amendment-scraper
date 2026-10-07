<# 
.SYNOPSIS
    Uninstalls the BDK Amendment Scraper Windows Service.

.DESCRIPTION
    Stops and removes the service installed by setup-stable-service.ps1.

.EXAMPLE
    .\uninstall-service.ps1
#>

param(
    [string]$ServiceName = "BdkAmendmentScraper",
    [string]$NssmExe = "$env:ProgramFiles\nssm\nssm.exe"
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  BDK Amendment Scraper - Service Uninstall" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check for Admin rights
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Error "This script must be run as Administrator!"
    exit 1
}

# Stop service if running
Write-Host "[1/3] Stopping service..." -ForegroundColor Yellow
try {
    Stop-Service -Name $ServiceName -Force -ErrorAction Stop
    Write-Host "  Service stopped" -ForegroundColor Green
} catch {
    Write-Warning "Service was not running or already stopped"
}

# Remove service
Write-Host "[2/3] Removing service..." -ForegroundColor Yellow
if (Test-Path $NssmExe) {
    & $NssmExe remove $ServiceName confirm
    Write-Host "  Service removed" -ForegroundColor Green
} else {
    Write-Warning "NSSM not found, trying sc.exe"
    sc.exe delete $ServiceName
}

# Clean up failure actions
Write-Host "[3/3] Cleaning up..." -ForegroundColor Yellow
sc.exe failure $ServiceName reset= 0 actions= "" 2>$null

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  Service Uninstall Complete!" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan