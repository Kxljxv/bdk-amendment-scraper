<# 
.SYNOPSIS
    Hardens Windows 11 for 24/7 unattended operation as a Mini-PC server.

.DESCRIPTION
    Configures power settings, disables sleep/hibernation, optimizes network,
    configures Windows Update, enables RDP, and sets up auto-login.

.REQUIREMENTS
    - Run as Administrator
    - Windows 11 Pro/Enterprise recommended

.NOTES
    Run this once after fresh Windows install, before deploying the scraper.
#>

param(
    [string]$AutoLoginUser = $env:USERNAME,
    [string]$AutoLoginPassword = "",
    [string]$NasSharePath = "",
    [bool]$EnableRdp = $true
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Windows 11 Hardening for 24/7 Mini-PC Operation" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Check for Admin rights
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Write-Error "This script must be run as Administrator!"
    exit 1
}

# 1. Power Settings - Disable Sleep/Hibernation
Write-Host "[1/10] Configuring power settings..." -ForegroundColor Yellow
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /hibernate off
powercfg /setactive SCHEME_MIN  # High performance
Write-Host "  Sleep, monitor off, hibernation disabled" -ForegroundColor Green

# 2. Network - Disable power saving on NIC
Write-Host "[2/10] Optimizing network adapter..." -ForegroundColor Yellow
$Adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" -and $_.InterfaceType -eq 6 }
foreach ($Adapter in $Adapters) {
    $Name = $Adapter.Name
    Set-NetAdapterPowerManagement -Name $Name -Enabled $false -ErrorAction SilentlyContinue
    Set-NetAdapterAdvancedProperty -Name $Name -DisplayName "Energy Efficient Ethernet" -DisplayValue "Disabled" -ErrorAction SilentlyContinue
    Set-NetAdapterAdvancedProperty -Name $Name -DisplayName "Green Ethernet" -DisplayValue "Disabled" -ErrorAction SilentlyContinue
    Set-NetAdapterAdvancedProperty -Name $Name -DisplayName "Power Saving Mode" -DisplayValue "Disabled" -ErrorAction SilentlyContinue
    Write-Host "  Optimized: $Name" -ForegroundColor Green
}

# 3. Windows Update - Configure for minimal disruption
Write-Host "[3/10] Configuring Windows Update..." -ForegroundColor Yellow
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "NoAutoUpdate" -Value 0 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "AUOptions" -Value 3 -Force  # 3 = Notify before download
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "ScheduledInstallDay" -Value 0 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "ScheduledInstallTime" -Value 3 -Force  # 3 AM
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "AutoInstallMinorUpdates" -Value 1 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "RebootRelaunchTimeoutEnabled" -Value 1 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "RebootRelaunchTimeout" -Value 1440 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "RebootWarningTimeoutEnabled" -Value 1 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "RebootWarningTimeout" -Value 60 -Force
Write-Host "  Update policy: Notify before download, install at 3 AM" -ForegroundColor Green

# 4. Disable unnecessary services
Write-Host "[4/10] Disabling unnecessary services..." -ForegroundColor Yellow
$ServicesToDisable = @(
    "DiagTrack",           # Connected User Experiences
    "dmwappushservice",    # Device Management
    "WSearch",             # Windows Search (if not needed)
    "SysMain",             # Superfetch
    "PrintWorkflowService" # Print workflow
)
foreach ($Svc in $ServicesToDisable) {
    try {
        Set-Service -Name $Svc -StartupType Disabled -ErrorAction Stop
        Stop-Service -Name $Svc -Force -ErrorAction SilentlyContinue
        Write-Host "  Disabled: $Svc" -ForegroundColor Green
    } catch {
        Write-Host "  Skipped: $Svc (not found or access denied)" -ForegroundColor Gray
    }
}

# 5. Enable RDP
if ($EnableRdp) {
    Write-Host "[5/10] Enabling Remote Desktop..." -ForegroundColor Yellow
    Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" -Name "fDenyTSConnections" -Value 0 -Force
    Enable-NetFirewallRule -DisplayGroup "Remote Desktop" -ErrorAction SilentlyContinue
    Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" -Name "UserAuthentication" -Value 1 -Force
    Write-Host "  RDP enabled with NLA" -ForegroundColor Green
} else {
    Write-Host "[5/10] Skipping RDP (disabled by parameter)" -ForegroundColor Gray
}

# 6. Auto-Login (optional)
if ($AutoLoginPassword) {
    Write-Host "[6/10] Configuring auto-login for $AutoLoginUser..." -ForegroundColor Yellow
    $RegPath = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
    Set-ItemProperty -Path $RegPath -Name "AutoAdminLogon" -Value 1 -Force
    Set-ItemProperty -Path $RegPath -Name "DefaultUserName" -Value $AutoLoginUser -Force
    Set-ItemProperty -Path $RegPath -Name "DefaultPassword" -Value $AutoLoginPassword -Force
    Set-ItemProperty -Path $RegPath -Name "DefaultDomainName" -Value $env:USERDOMAIN -Force
    Write-Host "  Auto-login configured" -ForegroundColor Green
    Write-Warning "Password stored in registry in plain text. Consider security implications."
} else {
    Write-Host "[6/10] Skipping auto-login (no password provided)" -ForegroundColor Gray
}

# 7. Disable UAC for service account (if running as service)
Write-Host "[7/10] Configuring UAC for service operation..." -ForegroundColor Yellow
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name "EnableLUA" -Value 1 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name "ConsentPromptBehaviorAdmin" -Value 0 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System" -Name "EnableInstallerDetection" -Value 0 -Force
Write-Host "  UAC configured for silent elevation" -ForegroundColor Green

# 8. Configure Windows Error Reporting (disable crash dialogs)
Write-Host "[8/10] Configuring error reporting..." -ForegroundColor Yellow
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting" -Name "Disabled" -Value 1 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting" -Name "DontShowUI" -Value 1 -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting" -Name "AutoReconnect" -Value 1 -Force
Write-Host "  Crash dialogs disabled" -ForegroundColor Green

# 9. Set page file to fixed size (performance)
Write-Host "[9/10] Configuring page file..." -ForegroundColor Yellow
$PageFile = Get-CimInstance -ClassName Win32_PageFileSetting
if ($PageFile) {
    $PageFile.InitialSize = 4096
    $PageFile.MaximumSize = 4096
    $PageFile.Put()
    Write-Host "  Page file set to fixed 4GB" -ForegroundColor Green
}

# 10. Network share mapping (if NAS path provided)
if ($NasSharePath) {
    Write-Host "[10/10] Mapping NAS share..." -ForegroundColor Yellow
    $DriveLetter = "Z:"
    if (-not (Test-Path $DriveLetter)) {
        New-PSDrive -Name "Z" -PSProvider FileSystem -Root $NasSharePath -Persist -ErrorAction SilentlyContinue
        Write-Host "  Mapped $NasSharePath to Z:" -ForegroundColor Green
    } else {
        Write-Host "  Z: already mapped" -ForegroundColor Green
    }
} else {
    Write-Host "[10/10] Skipping NAS mapping (no path provided)" -ForegroundColor Gray
}

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "  Windows Hardening Complete!" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "REBOOT REQUIRED for all changes to take effect." -ForegroundColor Yellow
Write-Host ""
Write-Host "After reboot, verify:" -ForegroundColor White
Write-Host "  - powercfg /a  (should show sleep states unavailable)" -ForegroundColor Gray
Write-Host "  - Get-Service BdkAmendmentScraper  (should be Running)" -ForegroundColor Gray
Write-Host "  - RDP connection works" -ForegroundColor Gray
Write-Host ""