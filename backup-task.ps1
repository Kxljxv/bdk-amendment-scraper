<# 
.SYNOPSIS
    Daily backup script using robocopy to copy scraper archives to NAS.

.DESCRIPTION
    Copies the archive directory to a NAS share with verification, logging,
    and email notification on failure. Designed to run as a Scheduled Task.

.PARAMETERS
    -NasPath: UNC path to NAS share (e.g., \\NAS\Backups\bdk-scraper)
    -SourcePath: Local archive directory
    -LogPath: Log file location
    -RetentionDays: How many days of backups to keep on NAS

.SCHEDULE
    Create a Daily Scheduled Task (Task Scheduler) running at 03:30:
    Action: powershell.exe
    Arguments: -ExecutionPolicy Bypass -File "C:\path\backup-task.ps1" -NasPath "\\NAS\Backups\bdk-scraper"
    Run whether user is logged on or not, Run with highest privileges
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$NasPath,

    [string]$SourcePath = "C:\Users\kolja\Desktop\52bdk\output\archive",
    [string]$LogPath = "C:\Users\kolja\Desktop\52bdk\output\backup.log",
    [int]$RetentionDays = 30,
    [string]$EmailFrom = "",
    [string]$EmailTo = "",
    [string]$SmtpServer = "",
    [int]$SmtpPort = 587,
    [string]$SmtpUser = "",
    [string]$SmtpPass = ""
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Entry = "[$Timestamp] [$Level] $Message"
    Add-Content -Path $LogPath -Value $Entry -Encoding UTF8
    Write-Host $Entry -ForegroundColor (if ($Level -eq "ERROR") { "Red" } elseif ($Level -eq "WARN") { "Yellow" } else { "Green" })
}

function Send-Notification {
    param([string]$Subject, [string]$Body, [bool]$IsError = $false)
    if (-not $EmailTo -or -not $SmtpServer) { return }
    
    try {
        $Mail = @{
            From = $EmailFrom
            To = $EmailTo
            Subject = "[BDK Scraper Backup] $Subject"
            Body = $Body
            SmtpServer = $SmtpServer
            Port = $SmtpPort
            UseSsl = $true
            Credential = (New-Object System.Management.Automation.PSCredential($SmtpUser, (ConvertTo-SecureString $SmtpPass -AsPlainText -Force)))
        }
        Send-MailMessage @Mail
    } catch {
        Write-Log "Failed to send email notification: $_" "WARN"
    }
}

# Start
Write-Log "===== Backup Started ===="
Write-Log "Source: $SourcePath"
Write-Log "Destination: $NasPath"

# Verify source exists
if (-not (Test-Path $SourcePath)) {
    Write-Log "Source directory does not exist: $SourcePath" "ERROR"
    Send-Notification "FAILED - Source Missing" "Source directory not found: $SourcePath" $true
    exit 1
}

# Verify NAS is reachable
$NasRoot = $NasPath.Split('\')[2]
if (-not (Test-Connection -ComputerName $NasRoot -Count 1 -Quiet)) {
    Write-Log "NAS not reachable: $NasRoot" "ERROR"
    Send-Notification "FAILED - NAS Unreachable" "Cannot reach NAS at $NasRoot" $true
    exit 1
}

# Create destination directory
$DestPath = "$NasPath\$(Get-Date -Format 'yyyy-MM')"
if (-not (Test-Path $DestPath)) {
    New-Item -ItemType Directory -Path $DestPath -Force | Out-Null
    Write-Log "Created destination directory: $DestPath"
}

# Robocopy with options:
# /MIR = Mirror (delete extraneous files on destination)
# /R:3 /W:10 = Retry 3 times, wait 10 seconds
# /LOG+: Append to log file
# /TEE = Output to console and log
# /COPY:DATSO = Copy Data, Attributes, Timestamps, Security, Owner
# /DCOPY:T = Copy directory timestamps
# /MT:8 = Multi-threaded (8 threads)
$RobocopyArgs = @(
    $SourcePath,
    $DestPath,
    "/MIR",
    "/R:3",
    "/W:10",
    "/MT:8",
    "/COPY:DATSO",
    "/DCOPY:T",
    "/LOG+:$LogPath",
    "/TEE",
    "/NP",  # No progress
    "/NFL", # No file list
    "/NDL"  # No directory list
)

Write-Log "Starting robocopy..."
$ExitCode = & robocopy @RobocopyArgs

# Robocopy exit codes:
# 0 = No files copied, no failures
# 1 = Files copied successfully
# 2 = Extra files detected
# 3 = Files copied + extra files
# 4 = Some mismatched files
# 5 = Files copied + mismatched
# 6 = Extra + mismatched
# 7 = Files copied + extra + mismatched
# 8+ = Errors

$Success = $ExitCode -le 3

if ($Success) {
    Write-Log "Robocopy completed successfully (exit code: $ExitCode)"
    
    # Count files backed up
    $FileCount = (Get-ChildItem -Path $DestPath -Recurse -File | Measure-Object).Count
    $TotalSize = (Get-ChildItem -Path $DestPath -Recurse -File | Measure-Object -Property Length -Sum).Sum
    $SizeMB = [math]::Round($TotalSize / 1MB, 2)
    
    Write-Log "Backed up $FileCount files ($SizeMB MB)"
    Send-Notification "SUCCESS" "Backup completed: $FileCount files ($SizeMB MB) copied to $DestPath"
} else {
    Write-Log "Robocopy failed with exit code: $ExitCode" "ERROR"
    Send-Notification "FAILED - Robocopy Error" "Robocopy exited with code $ExitCode. Check log: $LogPath" $true
    exit 1
}

# Cleanup old backups on NAS (older than RetentionDays)
Write-Log "Cleaning up backups older than $RetentionDays days..."
$Cutoff = (Get-Date).AddDays(-$RetentionDays)
$OldDirs = Get-ChildItem -Path $NasPath -Directory | Where-Object { $_.LastWriteTime -lt $Cutoff }
foreach ($Dir in $OldDirs) {
    try {
        Remove-Item -Path $Dir.FullName -Recurse -Force -ErrorAction Stop
        Write-Log "Removed old backup: $($Dir.Name)"
    } catch {
        Write-Log "Failed to remove $($Dir.Name): $_" "WARN"
    }
}

Write-Log "===== Backup Completed Successfully ===="
exit 0