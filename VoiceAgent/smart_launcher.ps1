# Frontbit Link & AI Voice Bot - Smart Launcher & Auto-Installer
# Automatically checks dependencies, displays professional download progress if on a new PC,
# and starts Frontbit Link + AI Voice Bot in 1 click. Subsequent runs start instantly with 0 downloads.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RootDir = Split-Path -Parent $ScriptDir
$SefirahExe = Join-Path $RootDir "Sefirah.exe"
$VenvDir = Join-Path $ScriptDir "venv"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
$ReqFile = Join-Path $ScriptDir "AIPhoneVoiceBot\requirements.txt"
$BotScript = Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC\phone_link_no_adb_bot.py"
$LogoPath = Join-Path $RootDir "Logo.png"

# Ensure Windows Start Menu & Desktop Shortcuts exist
function Ensure-Shortcuts {
    param([string]$targetCmd)
    try {
        $wsh = New-Object -ComObject WScript.Shell
        $startMenu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Frontbit Link.lnk"
        $desktop = "$env:USERPROFILE\Desktop\Frontbit Link.lnk"
        
        foreach ($scPath in @($startMenu, $desktop)) {
            if (-not (Test-Path $scPath)) {
                $sc = $wsh.CreateShortcut($scPath)
                $sc.TargetPath = $targetCmd
                $sc.WorkingDirectory = $RootDir
                $sc.IconLocation = "$SefirahExe,0"
                $sc.Description = "Frontbit Link - AI Phone Voice Agent"
                $sc.Save()
            }
        }
    } catch {}
}

# Find valid python with required modules
function Find-Ready-Python {
    if (Test-Path $VenvPy) {
        $chk = & $VenvPy -c "import faster_whisper, edge_tts, bottle, scipy; print('OK')" 2>$null
        if ($chk -match "OK") { return $VenvPy }
    }
    $sysPy = (Get-Command python.exe -ErrorAction SilentlyContinue)
    if ($sysPy) {
        $chk = & $sysPy.Source -c "import faster_whisper, edge_tts, bottle, scipy; print('OK')" 2>$null
        if ($chk -match "OK") { return $sysPy.Source }
    }
    return $null
}

# Fast path: If already installed and ready, launch instantly with 0 downloads!
$readyPy = Find-Ready-Python
$targetLauncher = Join-Path $RootDir "1 - START EVERYTHING (Frontbit Link + Voice Bot).cmd"

if ($readyPy -and (Test-Path $SefirahExe)) {
    Ensure-Shortcuts $targetLauncher
    
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "   FRONTBIT LINK AND AI VOICE BOT ARE STARTING..." -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Cyan

    # 100% Pure Wired Audio Mode (No Bluetooth)
    Write-Host "[+] AI Voice Mode: 100% PURE WIRED HD AUDIO (Zero Delay)" -ForegroundColor Green
    Write-Host "    Audio Route: Phone Mic <-> Realtek PC Audio (Mic In & Speaker Out)" -ForegroundColor Cyan
    Write-Host "[+] STT Engine: Google Speech Recognition (Primary) + Faster-Whisper (Backup)" -ForegroundColor Green
    Write-Host "[+] Agent Brain: BestZone Catalog & FAQ Semantic Matcher" -ForegroundColor Green
    Write-Host "[+] Local TTS Engine: Mithi v2 Flash (Active Voice: anirban - Male)" -ForegroundColor Green

    $PlatformTools = Join-Path $RootDir "platform-tools"
    if (Test-Path $PlatformTools) {
        $env:PATH = "$PlatformTools;" + $env:PATH
    }
    $userAdbHome = Join-Path $env:USERPROFILE ".android"
    $env:ANDROID_USER_HOME = if (Test-Path (Join-Path $userAdbHome "adbkey")) { $userAdbHome } else { (Join-Path $RootDir ".android") }

    # Clean up lingering old bot, chat, and dashboard instances
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { 
        ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and 
        ($_.CommandLine -match "phone_link_no_adb_bot" -or $_.CommandLine -match "faq_voice_start_page" -or $_.CommandLine -match "live_chat_viewer" -or $_.CommandLine -match "Frontbit Link") -and 
        $_.ProcessId -ne $PID 
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 300

    # Reset live_chat.json so stale hallucinated text is cleared
    $liveChatJson = Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC\live_chat.json"
    if (Test-Path $liveChatJson) {
        try { '{"events":[],"updated":""}' | Set-Content $liveChatJson -Force } catch {}
    }

    # Ensure default tab is Messages (not Bluetooth Calls page)
    $localSettingsPaths = Get-ChildItem -Path "$env:LOCALAPPDATA\Packages" -Filter "local-settings.json" -Recurse -ErrorAction SilentlyContinue
    foreach ($sp in $localSettingsPaths) {
        try {
            $content = Get-Content $sp.FullName -Raw | ConvertFrom-Json
            if ($content.MainNavigationSelection -eq "Calls") {
                $content.MainNavigationSelection = "Messages"
                $content | ConvertTo-Json -Compress | Set-Content $sp.FullName -Force
            }
        } catch {}
    }

    # 1. Start Frontbit Link Desktop App (ensure fresh visible window using native standalone executable)
    Write-Host "1. Launching Frontbit Link Desktop App..." -ForegroundColor Yellow
    Get-Process Sefirah -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 400
    Start-Process -FilePath $SefirahExe -WorkingDirectory $RootDir
    Start-Sleep -Seconds 1
    
    # 2. Start Dashboard Web Server in background (port 8765)
    Write-Host "2. Starting FAQ & Bot Dashboard on port 8765..." -ForegroundColor Yellow
    $dashScript = Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC\faq_voice_start_page.py"
    if (Test-Path $dashScript) {
        $dashPortListening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
        if (-not $dashPortListening) {
            Start-Process -FilePath $readyPy -ArgumentList "-u `"$dashScript`" --server 8765" -WorkingDirectory (Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC") -WindowStyle Hidden
        }
    }

    # 3. Start Live Chat Viewer Screen
    Write-Host "3. Opening Live Call Chat Monitor..." -ForegroundColor Yellow
    $chatViewerScript = Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC\live_chat_viewer.py"
    if (Test-Path $chatViewerScript) {
        Start-Process -FilePath $readyPy -ArgumentList "`"$chatViewerScript`"" -WorkingDirectory (Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC")
    }

    # 4. Start AI Voice Bot
    Write-Host "4. Starting AI Voice Bot (Auto-Answer & Pure HD Voice)..." -ForegroundColor Yellow
    Write-Host "Bot is LIVE! Waiting for incoming calls..." -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host ""
    
    $userAdbHome = Join-Path $env:USERPROFILE ".android"
    $env:ANDROID_USER_HOME = if (Test-Path (Join-Path $userAdbHome "adbkey")) { $userAdbHome } else { (Join-Path $RootDir ".android") }
    Set-Location (Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC")
    & $readyPy -u $BotScript --fast-start
    exit 0
}

# -------------------------------------------------------------
# First-time Setup: Professional GUI Progress Window
# -------------------------------------------------------------
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$Form = New-Object System.Windows.Forms.Form
$Form.Text = "Frontbit Link - Setup & Voice Agent Installer"
$Form.Size = New-Object System.Drawing.Size(560, 310)
$Form.StartPosition = "CenterScreen"
$Form.FormBorderStyle = "FixedDialog"
$Form.MaximizeBox = $false
$Form.BackColor = [System.Drawing.Color]::FromArgb(26, 27, 38)
$Form.ForeColor = [System.Drawing.Color]::White

# Title Label
$LblTitle = New-Object System.Windows.Forms.Label
$LblTitle.Text = "Frontbit Link AI Voice Agent"
$LblTitle.Font = New-Object System.Drawing.Font("Segoe UI", 16, [System.Drawing.FontStyle]::Bold)
$LblTitle.Location = New-Object System.Drawing.Point(30, 25)
$LblTitle.Size = New-Object System.Drawing.Size(490, 38)
$LblTitle.ForeColor = [System.Drawing.Color]::FromArgb(240, 240, 255)
$Form.Controls.Add($LblTitle)

# Subtitle / Current Action Label
$LblAction = New-Object System.Windows.Forms.Label
$LblAction.Text = "Checking system environment..."
$LblAction.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$LblAction.Location = New-Object System.Drawing.Point(32, 70)
$LblAction.Size = New-Object System.Drawing.Size(490, 28)
$LblAction.ForeColor = [System.Drawing.Color]::FromArgb(160, 165, 195)
$Form.Controls.Add($LblAction)

# Progress Bar
$ProgressBar = New-Object System.Windows.Forms.ProgressBar
$ProgressBar.Location = New-Object System.Drawing.Point(32, 115)
$ProgressBar.Size = New-Object System.Drawing.Size(480, 28)
$ProgressBar.Style = "Continuous"
$ProgressBar.Value = 0
$Form.Controls.Add($ProgressBar)

# Stats Label (Percentage, MB, Speed)
$LblStats = New-Object System.Windows.Forms.Label
$LblStats.Text = "Preparing components..."
$LblStats.Font = New-Object System.Drawing.Font("Consolas", 9.5)
$LblStats.Location = New-Object System.Drawing.Point(32, 155)
$LblStats.Size = New-Object System.Drawing.Size(480, 25)
$LblStats.ForeColor = [System.Drawing.Color]::FromArgb(130, 140, 180)
$Form.Controls.Add($LblStats)

# Detail / Info Footer Label
$LblFooter = New-Object System.Windows.Forms.Label
$LblFooter.Text = "First-time setup only. Future runs will start instantly in 1 click."
$LblFooter.Font = New-Object System.Drawing.Font("Segoe UI", 8.5)
$LblFooter.Location = New-Object System.Drawing.Point(32, 220)
$LblFooter.Size = New-Object System.Drawing.Size(480, 22)
$LblFooter.ForeColor = [System.Drawing.Color]::FromArgb(100, 105, 135)
$Form.Controls.Add($LblFooter)

function Update-UI {
    param([string]$action, [int]$percent, [string]$stats)
    $LblAction.Text = $action
    $ProgressBar.Value = [Math]::Min(100, [Math]::Max(0, $percent))
    if ($stats) { $LblStats.Text = $stats }
    $Form.Refresh()
    [System.Windows.Forms.Application]::DoEvents()
}

$Form.Add_Shown({
    $bgWorker = New-Object System.ComponentModel.BackgroundWorker
    $bgWorker.WorkerReportsProgress = $true
    
    $bgWorker.DoWork = {
        # 1. Download file with live progress (percent, MB, speed)
        function Download-With-Progress {
            param([string]$url, [string]$destPath, [string]$componentName)
            
            $req = [System.Net.HttpWebRequest]::Create($url)
            $req.Timeout = 30000
            $resp = $req.GetResponse()
            $totalBytes = $resp.ContentLength
            $stream = $resp.GetResponseStream()
            $fs = New-Object System.IO.FileStream($destPath, [System.IO.FileMode]::Create)
            
            $buf = New-Object byte[] 65536
            $readBytes = 0
            $startTime = [System.DateTime]::Now
            $lastUpdate = [System.DateTime]::Now
            
            while (($count = $stream.Read($buf, 0, $buf.Length)) -gt 0) {
                $fs.Write($buf, 0, $count)
                $readBytes += $count
                
                $now = [System.DateTime]::Now
                if (($now - $lastUpdate).TotalMilliseconds -gt 250 -or $readBytes -eq $totalBytes) {
                    $lastUpdate = $now
                    $percent = if ($totalBytes -gt 0) { [int](($readBytes / $totalBytes) * 100) } else { 50 }
                    $elapsedSec = ($now - $startTime).TotalSeconds
                    $speedMBs = if ($elapsedSec -gt 0) { (($readBytes / (1024*1024)) / $elapsedSec) } else { 0 }
                    $mbRead = [Math]::Round($readBytes / (1024*1024), 1)
                    $mbTotal = [Math]::Round($totalBytes / (1024*1024), 1)
                    
                    $statStr = "$percent% | $mbRead MB / $mbTotal MB | $([Math]::Round($speedMBs, 1)) MB/s"
                    Update-UI "Downloading $componentName..." $percent $statStr
                }
            }
            $fs.Close()
            $stream.Close()
            $resp.Close()
        }

        # Check Python installation
        Update-UI "Checking Python installation..." 10 "Scanning system..."
        Start-Sleep -Milliseconds 400

        $foundPy = $null
        $sysCmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($sysCmd) {
            $ver = & $sysCmd.Source --version 2>&1
            if ($ver -match "Python 3\.(1[0-9]|[2-9])") {
                $foundPy = $sysCmd.Source
            }
        }

        if (-not $foundPy) {
            # Download and install official Python 3.11.9
            $pyInstaller = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
            $pyUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
            Download-With-Progress $pyUrl $pyInstaller "Python 3.11.9"
            
            Update-UI "Installing Python 3.11..." 40 "Configuring system PATH..."
            $proc = Start-Process $pyInstaller -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 SimpleInstall=1" -Wait -PassThru
            Remove-Item $pyInstaller -ErrorAction SilentlyContinue
            
            # Refresh PATH
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")
            $sysCmd = Get-Command python.exe -ErrorAction SilentlyContinue
            $foundPy = if ($sysCmd) { $sysCmd.Source } else { "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python311\python.exe" }
        }

        # Set up local Virtual Environment in VoiceAgent\venv
        if (-not (Test-Path $VenvPy)) {
            Update-UI "Creating AI Voice Virtual Environment..." 50 "Setting up isolated workspace..."
            & $foundPy -m venv $VenvDir
        }

        # Upgrade pip & install requirements
        Update-UI "Installing Voice AI packages (faster-whisper, edge-tts, soundfile)..." 65 "Resolving dependencies..."
        & $VenvPy -m pip install --upgrade pip --quiet
        & $VenvPy -m pip install -r $ReqFile --quiet

        # Check AI Models
        Update-UI "Verifying AI Speech Models..." 90 "Checking neural weights..."
        $modelsDir = Join-Path $ScriptDir "AIPhoneVoiceBot\models"
        if (-not (Test-Path $modelsDir) -or (Get-ChildItem $modelsDir).Count -eq 0) {
            Update-UI "Downloading Speech Recognition Models..." 92 "Fetching weights..."
            & $VenvPy -c "from faster_whisper import WhisperModel; WhisperModel('small', download_root=r'$modelsDir')"
        }

        # Check Mithi v2 Flash Model
        $mithiDir = Join-Path $RootDir "models\mithi-v2-flash"
        if (-not (Test-Path $mithiDir) -or (Get-ChildItem $mithiDir).Count -lt 5) {
            Update-UI "Downloading Mithi v2 Flash Local Hindi TTS Model..." 93 "Fetching Oomni/mithi-v2-flash..."
            & $VenvPy -c "from huggingface_hub import snapshot_download; snapshot_download('Oomni/mithi-v2-flash', local_dir=r'$mithiDir', resume_download=True)"
        }

        # Register Frontbit Link Windows App (MSIX) if not installed
        $appx = Get-AppxPackage -Name "shrimqy.Seki-PhoneLink" -ErrorAction SilentlyContinue
        $msixPath = Join-Path $RootDir "Sefirah-trusted.msixbundle"
        if (-not $appx -and (Test-Path $msixPath)) {
            Update-UI "Registering Frontbit Link Windows App..." 95 "Installing app bundle..."
            Add-AppxPackage -Path $msixPath -ErrorAction SilentlyContinue
        }

        # Create Windows Start Menu & Desktop Shortcuts
        Update-UI "Registering Windows Start Menu & Desktop App..." 98 "Creating shortcuts..."
        Ensure-Shortcuts $targetLauncher

        Update-UI "Setup Complete! Starting Frontbit Link..." 100 "All components verified!"
        Start-Sleep -Seconds 1
        $Form.Close()
    }

    $bgWorker.RunWorkerAsync()
})

$Form.ShowDialog() | Out-Null

# Post-setup: Launch Frontbit Link & Voice Bot
$finalPy = Find-Ready-Python
if (-not $finalPy) { $finalPy = $VenvPy }

$appx = Get-AppxPackage -Name "shrimqy.Seki-PhoneLink" -ErrorAction SilentlyContinue
if ($appx) {
    Start-Process "explorer.exe" "shell:AppsFolder\$($appx.PackageFamilyName)!App"
} else {
    Start-Process -FilePath $SefirahExe -WorkingDirectory $RootDir
}
Start-Sleep -Seconds 1

# Start Dashboard Web Server in background (port 8765)
$dashScript = Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC\faq_voice_start_page.py"
if (Test-Path $dashScript) {
    $dashPortListening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if (-not $dashPortListening) {
        Start-Process -FilePath $finalPy -ArgumentList "-u `"$dashScript`" --server 8765" -WorkingDirectory (Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC") -WindowStyle Hidden
    }
}

# Start Live Chat Viewer Screen
$chatViewerScript = Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC\live_chat_viewer.py"
if (Test-Path $chatViewerScript) {
    Start-Process -FilePath $finalPy -ArgumentList "`"$chatViewerScript`"" -WorkingDirectory (Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC")
}

$userAdbHome = Join-Path $env:USERPROFILE ".android"
$env:ANDROID_USER_HOME = if (Test-Path (Join-Path $userAdbHome "adbkey")) { $userAdbHome } else { (Join-Path $RootDir ".android") }
Set-Location (Join-Path $ScriptDir "AIPhoneVoiceBot_NoADB_POC")
& $finalPy -u $BotScript --fast-start
