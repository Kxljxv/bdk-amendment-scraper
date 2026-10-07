<# 
.SYNOPSIS
    Installs the Antragsgruen Amendment Scraper as a Windows Service using NSSM.

.DESCRIPTION
    This script installs NSSM (Non-Sucking Service Manager), registers the scraper
    as a Windows service, and configures automatic restart on failure.

.PREREQUISITES
    - Run as Administrator
    - Python 3.10+ installed and in PATH
    - NSSM will be downloaded if not present

.EXAMPLE
    .\setup-stable-service.ps1
#>

param(
    [string]$PythonPath = "python",
    [string]$ProjectPath = "C:\Users\kolja\Desktop\52bdk",
    [string]$ServiceName = "BdkAmendmentScraper",
    [string]$DisplayName = "BDK Amendment Scraper (52bdk)",
    [string]$Description = "Periodically scrapes amendments from Antragsgruen for BDK 52 and exports to Google Sheets"
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  BDK Amendment Scraper - Windows Service Setup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check for Admin rights
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Error "This script must be run as Administrator!"
    exit 1
}

# Configuration
$NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
$NssmDir = "$env:ProgramFiles\nssm"
$NssmExe = "$NssmDir\nssm.exe"
$PythonExe = (Get-Command $PythonPath -ErrorAction SilentlyContinue).Source
$MainScript = "$ProjectPath\scraper\main.py"

if (-not $PythonExe) {
    Write-Error "Python not found in PATH. Please install Python 3.10+ and add to PATH."
    exit 1
}

Write-Host "[Config] Python: $PythonExe" -ForegroundColor Green
Write-Host "[Config] Project: $ProjectPath" -ForegroundColor Green
Write-Host "[Config] Main script: $MainScript" -ForegroundColor Green
Write-Host "[Config] Service: $ServiceName" -ForegroundColor Green

# Check if main script exists
if (-not (Test-Path $MainScript)) {
    Write-Error "Main script not found: $MainScript"
    exit 1
}

# Download and install NSSM
Write-Host "`n[1/6] Installing NSSM..." -ForegroundColor Yellow
if (-not (Test-Path $NssmExe)) {
    Write-Host "  Downloading NSSM from $NssmUrl..."
    $TempZip = "$env:TEMP\nssm.zip"
    try {
        Invoke-WebRequest -Uri $NssmUrl -OutFile $TempZip -UseBasicParsing
        Expand-Archive -Path $TempZip -DestinationPath "$env:TEMP\nssm" -Force
        $SourceExe = "$env:TEMP\nssm\nssm-2.24\win64\nssm.exe"
        if (Test-Path $SourceExe) {
            New-Item -ItemType Directory -Path $NssmDir -Force | Out-Null
            Copy-Item $SourceExe $NssmExe -Force
            Write-Host "  NSSM installed to $NssmExe" -ForegroundColor Green
        } else {
            Write-Error "NSSM executable not found in archive"
            exit 1
        }
    } catch {
        Write-Error "Failed to download/install NSSM: $_"
        exit 1
    } finally {
        if (Test-Path $TempZip) { Remove-Item $TempZip -Force }
        if (Test-Path "$env:TEMP\nssm") { Remove-Item "$env:TEMP\nssm" -Recurse -Force }
    }
} else {
    Write-Host "  NSSM already installed at $NssmExe" -ForegroundColor Green
}

# Install service
Write-Host "`n[2/6] Installing Windows Service '$ServiceName'..." -ForegroundColor Yellow
$Args = "`"$MainScript`" --interval 10 --workers 10"
try {
    & $NssmExe install $ServiceName $PythonExe $Args
    Write-Host "  Service installed" -ForegroundColor Green
} catch {
    Write-Error "Failed to install service: $_"
    exit 1
}

# Configure service
Write-Host "`n[3/6] Configuring service..." -ForegroundColor Yellow
& $NssmExe set $ServiceName DisplayName $DisplayName
& $NssmExe set $ServiceName Description $Description
& $NssmExe set $ServiceName AppDirectory $ProjectPath
& $NssmExe set $ServiceName AppStdout "$ProjectPath\output\service-stdout.log"
& $NssmExe set $ServiceName AppStderr "$ProjectPath\output\service-stderr.log"
& $NssmExe set $ServiceName AppRotateFiles 1
& $NssmExe set $ServiceName AppRotateOnline 1
& $NssmExe set $ServiceName AppRotateSeconds 86400

# Restart on failure
& $NssmExe set $ServiceName AppExit Default Restart
& $NssmExe set $ServiceName AppThrottle 5000
Write-Host "  Service configured" -ForegroundColor Green

# Configure recovery actions
Write-Host "`n[4/6] Configuring failure recovery..." -ForegroundColor Yellow
$FailureActions = @(
    @{ Type = "Restart"; Delay = 5000 },    # 1st failure: restart after 5s
    @{ Type = "Restart"; Delay = 30000 },   # 2nd failure: restart after 30s
    @{ Type = "Restart"; Delay = 60000 }    # 3rd failure: restart after 60s
)
& $NssmExe set $ServiceName AppExit Reset 86400  # Reset failure count after 24h
sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/30000/restart/60000
Write-Host "  Recovery actions configured" -ForegroundColor Green

# Set startup type to Automatic (Delayed)
Write-Host "`n[5/6] Setting startup type..." -ForegroundColor Yellow
Set-Service -Name $ServiceName -StartupType AutomaticDelayedStart
Write-Host "  Startup type: Automatic (Delayed Start)" -ForegroundColor Green

# Start service
Write-Host "`n[6/6] Starting service..." -ForegroundColor Yellow
try {
    Start-Service -Name $ServiceName -ErrorAction Stop
    Write-Host "  Service started successfully" -ForegroundColor Green
} catch {
    Write-Warning "Could not start service automatically. Check logs: $ProjectPath\output\service-stderr.log"
}

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  Service Installation Complete!" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Service Name: $ServiceName" -ForegroundColor White
Write-Host "Display Name: $DisplayName" -ForegroundColor White
Write-Host "Logs: $ProjectPath\output\service-*.log" -ForegroundColor White
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Yellow
Write-Host "  Start:   net start $ServiceName" -ForegroundColor Gray
Write-Host "  Stop:    net stop $ServiceName" -ForegroundColor Gray
Write-Host "  Status:  sc query $ServiceName" -ForegroundColor Gray
Write-Host "  Logs:    Get-Content $ProjectPath\output\service-stdout.log -Wait" -ForegroundColor Gray
Write-Host "  Uninstall: .\uninstall-service.ps1" -ForegroundColor Gray
Write-Host ""