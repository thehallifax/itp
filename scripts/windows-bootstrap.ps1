$script:ITPMinimumPython = [Version]"3.9"
$script:ITPPythonPackage = "Python.Python.3.12"
$script:ITPPythonDownload = "https://www.python.org/downloads/windows/"
$script:ITPPinnedPythonVersion = "3.12.10"
$script:ITPPythonSignerPattern = '(^|,\s*)CN=Python Software Foundation(,|$)'
$script:ITPPythonInstallers = @{
    AMD64 = [PSCustomObject]@{
        Architecture = "AMD64"
        Version = "3.12.10"
        FileName = "python-3.12.10-amd64.exe"
        Url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
        Sha256 = "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
        Signer = "Python Software Foundation"
        InstallDirectory = "Programs\Python\Python312"
    }
    ARM64 = [PSCustomObject]@{
        Architecture = "ARM64"
        Version = "3.12.10"
        FileName = "python-3.12.10-arm64.exe"
        Url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-arm64.exe"
        Sha256 = "377ac8fd478987940088e879441e702a71b53164d2a1e6f1d51ff77a7e470258"
        Signer = "Python Software Foundation"
        InstallDirectory = "Programs\Python\Python312-arm64"
    }
}

function Invoke-ITPPythonProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    $Code = "import sys; print(str(sys.version_info[0])+'.'+str(sys.version_info[1])); raise SystemExit(0)"
    $QuotedCode = '"' + $Code.Replace('"', '\"') + '"'
    $Arguments = @($PrefixArguments) + @("-c", $QuotedCode)
    $StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $StartInfo.FileName = $Executable
    $StartInfo.Arguments = ($Arguments -join " ")
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.CreateNoWindow = $true

    try {
        $Process = New-Object System.Diagnostics.Process
        $Process.StartInfo = $StartInfo
        if (-not $Process.Start()) {
            return $null
        }
        $StandardOutput = $Process.StandardOutput.ReadToEnd().Trim()
        $null = $Process.StandardError.ReadToEnd()
        $Process.WaitForExit()
        if ($Process.ExitCode -ne 0 -or $StandardOutput -notmatch '^\d+\.\d+$') {
            return $null
        }
        return [Version]$StandardOutput
    }
    catch {
        return $null
    }
}

function Invoke-ITPPipProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$PrefixArguments = @()
    )
    try {
        & $Executable @PrefixArguments -m pip --version *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Get-ITPNativeArchitecture {
    param([string]$ArchitectureOverride)
    if ($ArchitectureOverride) {
        $Raw = $ArchitectureOverride
    }
    elseif ($env:PROCESSOR_ARCHITEW6432) {
        $Raw = $env:PROCESSOR_ARCHITEW6432
    }
    elseif ($env:PROCESSOR_ARCHITECTURE) {
        $Raw = $env:PROCESSOR_ARCHITECTURE
    }
    else {
        try {
            $Raw = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
        }
        catch {
            $Raw = ""
        }
    }
    switch -Regex ("$Raw".ToUpperInvariant()) {
        '^(AMD64|X64)$' { return "AMD64" }
        '^ARM64$' { return "ARM64" }
        '^(X86|I386|I686)$' { return "x86" }
        default {
            throw "Unsupported or ambiguous native Windows architecture '$Raw'. ITP detects AMD64, ARM64, and x86; deployment supports AMD64 and ARM64."
        }
    }
}

function Get-ITPPythonInstaller {
    param([string]$Architecture)
    $Native = Get-ITPNativeArchitecture -ArchitectureOverride $Architecture
    if (-not $script:ITPPythonInstallers.ContainsKey($Native)) {
        throw "No verified direct Python installer is configured for architecture $Native."
    }
    return $script:ITPPythonInstallers[$Native]
}

function Get-ITPPythonCandidates {
    param(
        [switch]$IncludeInstallLocations,
        [string]$Architecture
    )

    $Candidates = @(
        [PSCustomObject]@{ Name = "py"; Prefix = @("-3"); Label = "py -3"; Path = $null },
        [PSCustomObject]@{ Name = "python"; Prefix = @(); Label = "python"; Path = $null },
        [PSCustomObject]@{ Name = "python3"; Prefix = @(); Label = "python3"; Path = $null }
    )
    if ($IncludeInstallLocations -and $env:LOCALAPPDATA) {
        $Installers = @($script:ITPPythonInstallers.Values)
        if ($Architecture) {
            $Installers = @(Get-ITPPythonInstaller -Architecture $Architecture)
        }
        foreach ($Installer in $Installers) {
            $Candidates += [PSCustomObject]@{
                Name = $null
                Prefix = @()
                Label = "Python $($Installer.Version) $($Installer.Architecture) per-user install"
                Path = Join-Path $env:LOCALAPPDATA "$($Installer.InstallDirectory)\python.exe"
            }
        }
        $Candidates += [PSCustomObject]@{
            Name = $null
            Prefix = @()
            Label = "WinGet Python 3.12 install"
            Path = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\python.exe"
        }
    }
    return $Candidates
}

function Find-ITPPython {
    param(
        [switch]$IncludeInstallLocations,
        [string]$Architecture,
        [scriptblock]$CommandResolver = {
            param($Name)
            Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
                Select-Object -First 1
        },
        [scriptblock]$Probe = {
            param($Executable, $Prefix)
            Invoke-ITPPythonProbe -Executable $Executable -PrefixArguments $Prefix
        }
    )

    $Unsupported = @()
    foreach ($Candidate in (Get-ITPPythonCandidates `
            -IncludeInstallLocations:$IncludeInstallLocations -Architecture $Architecture)) {
        if ($Candidate.Path) {
            if (-not (Test-Path -LiteralPath $Candidate.Path -PathType Leaf)) {
                continue
            }
            $Executable = $Candidate.Path
        }
        else {
            $Command = & $CommandResolver $Candidate.Name
            if ($null -eq $Command) {
                continue
            }
            $Executable = $Command.Source
        }
        $Version = & $Probe $Executable $Candidate.Prefix
        if ($null -eq $Version) {
            continue
        }
        if ([Version]$Version -ge $script:ITPMinimumPython) {
            return [PSCustomObject]@{
                Selected = [PSCustomObject]@{
                    Executable = $Executable
                    Prefix = $Candidate.Prefix
                    Label = $Candidate.Label
                    Version = ([Version]$Version).ToString(2)
                }
                Unsupported = $Unsupported
            }
        }
        $Unsupported += "$($Candidate.Label) $(([Version]$Version).ToString(2))"
    }
    return [PSCustomObject]@{ Selected = $null; Unsupported = $Unsupported }
}

function Update-ITPProcessPath {
    param(
        [scriptblock]$PathReader = {
            param($Scope)
            [Environment]::GetEnvironmentVariable("Path", $Scope)
        }
    )
    $MachinePath = & $PathReader "Machine"
    $UserPath = & $PathReader "User"
    $env:Path = (@($MachinePath, $UserPath) | Where-Object { $_ }) -join ";"
}

function Test-ITPInteractiveDeployment {
    param(
        [string[]]$Arguments,
        [Nullable[bool]]$InteractiveOverride = $null
    )
    if ($null -ne $InteractiveOverride) {
        return [bool]$InteractiveOverride
    }
    $IsDeploy = $Arguments.Count -gt 0 -and $Arguments[0] -eq "deploy"
    $ExplicitlyNonInteractive = (
        $Arguments -contains "--non-interactive" -or
        $env:ITP_NON_INTERACTIVE -eq "1" -or
        $env:CI -eq "true")
    $InputRedirected = $false
    try {
        $InputRedirected = [Console]::IsInputRedirected
    }
    catch {
        $InputRedirected = $true
    }
    return (
        $IsDeploy -and [Environment]::UserInteractive -and
        -not $ExplicitlyNonInteractive -and -not $InputRedirected)
}

function Test-ITPFixedTimeEquals {
    param([string]$Actual, [string]$Expected)
    $Left = [Text.Encoding]::ASCII.GetBytes(("$Actual").ToLowerInvariant())
    $Right = [Text.Encoding]::ASCII.GetBytes(("$Expected").ToLowerInvariant())
    $Difference = $Left.Length -bxor $Right.Length
    $Length = [Math]::Max($Left.Length, $Right.Length)
    for ($Index = 0; $Index -lt $Length; $Index++) {
        $LeftByte = if ($Index -lt $Left.Length) { $Left[$Index] } else { 0 }
        $RightByte = if ($Index -lt $Right.Length) { $Right[$Index] } else { 0 }
        $Difference = $Difference -bor ($LeftByte -bxor $RightByte)
    }
    return $Difference -eq 0
}

function Invoke-ITPInstallerDownload {
    param(
        [Parameter(Mandatory = $true)]$Metadata,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $Uri = [Uri]$Metadata.Url
    if ($Uri.Scheme -ne "https" -or $Uri.Host -ne "www.python.org") {
        throw "Direct Python bootstrap refused an unapproved installer URL: $($Metadata.Url)"
    }
    try {
        $Response = Invoke-WebRequest -Uri $Uri -OutFile $Destination `
            -UseBasicParsing -MaximumRedirection 5 -TimeoutSec 120 -PassThru
    }
    catch {
        $Message = $_.Exception.Message
        if ($Message -match '(?i)407|proxy authentication') {
            throw "Python download failed because the proxy requires authentication: $Message"
        }
        if ($Message -match '(?i)certificate|trust|TLS|SSL') {
            throw "Python download failed TLS or certificate validation: $Message"
        }
        if ($Message -match '(?i)404|403|500|HTTP') {
            throw "Python download returned an HTTP error: $Message"
        }
        throw "Python download failed. Check internet and proxy connectivity to www.python.org: $Message"
    }
    $FinalUri = $Uri
    if ($Response -and $Response.BaseResponse -and $Response.BaseResponse.ResponseUri) {
        $FinalUri = [Uri]$Response.BaseResponse.ResponseUri
    }
    if ($FinalUri.Scheme -ne "https" -or $FinalUri.Host -ne "www.python.org") {
        throw "Python download was redirected to unapproved host '$($FinalUri.Host)'; only www.python.org is allowed."
    }
    return $FinalUri.AbsoluteUri
}

function Test-ITPInstallerIntegrity {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Metadata,
        [scriptblock]$HashReader = {
            param($InstallerPath)
            (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash
        },
        [scriptblock]$SignatureReader = {
            param($InstallerPath)
            Get-AuthenticodeSignature -LiteralPath $InstallerPath
        }
    )
    $ActualHash = "$(& $HashReader $Path)".ToLowerInvariant()
    $Signature = & $SignatureReader $Path
    $SignerSubject = if ($Signature -and $Signature.SignerCertificate) {
        "$($Signature.SignerCertificate.Subject)"
    } else { "" }
    $SignatureStatus = if ($Signature) { "$($Signature.Status)" } else { "Missing" }

    [Console]::Error.WriteLine("Python installer source: $($Metadata.Url)")
    [Console]::Error.WriteLine("Expected SHA-256: $($Metadata.Sha256)")
    [Console]::Error.WriteLine("Actual SHA-256:   $ActualHash")
    [Console]::Error.WriteLine("Authenticode status: $SignatureStatus")
    [Console]::Error.WriteLine("Authenticode signer: $SignerSubject")

    if (-not (Test-ITPFixedTimeEquals -Actual $ActualHash -Expected $Metadata.Sha256)) {
        throw "Python installer SHA-256 mismatch. The downloaded file will not be executed."
    }
    if ($SignatureStatus -ne "Valid") {
        throw "Python installer Authenticode signature is missing, invalid, or untrusted (status: $SignatureStatus)."
    }
    if ($SignerSubject -notmatch $script:ITPPythonSignerPattern) {
        throw "Python installer signer '$SignerSubject' does not match the expected Python Software Foundation publisher."
    }
    return [PSCustomObject]@{
        Hash = $ActualHash
        SignatureStatus = $SignatureStatus
        SignerSubject = $SignerSubject
    }
}

function Install-ITPDirectPython {
    param(
        [Parameter(Mandatory = $true)]$Metadata,
        [scriptblock]$DownloadRunner = {
            param($InstallerMetadata, $Destination)
            Invoke-ITPInstallerDownload -Metadata $InstallerMetadata -Destination $Destination
        },
        [scriptblock]$HashReader,
        [scriptblock]$SignatureReader,
        [scriptblock]$InstallerRunner = {
            param($Executable, $Arguments)
            $Process = Start-Process -FilePath $Executable -ArgumentList $Arguments `
                -PassThru -Wait
            return $Process.ExitCode
        },
        [scriptblock]$TempDirectoryFactory = {
            Join-Path ([IO.Path]::GetTempPath()) ("itp-python-" + [Guid]::NewGuid().ToString("N"))
        },
        [scriptblock]$CleanupRunner = {
            param($Path)
            if (Test-Path -LiteralPath $Path) {
                Remove-Item -LiteralPath $Path -Recurse -Force
            }
        }
    )
    $TemporaryDirectory = & $TempDirectoryFactory
    $InstallerPath = Join-Path $TemporaryDirectory $Metadata.FileName
    try {
        $null = New-Item -ItemType Directory -Path $TemporaryDirectory -Force
        $FinalUrl = & $DownloadRunner $Metadata $InstallerPath
        if ($FinalUrl) {
            $FinalUri = [Uri]"$FinalUrl"
            if ($FinalUri.Scheme -ne "https" -or $FinalUri.Host -ne "www.python.org") {
                throw "Python download was redirected to unapproved host '$($FinalUri.Host)'."
            }
        }
        $IntegrityArguments = @{ Path = $InstallerPath; Metadata = $Metadata }
        if ($HashReader) { $IntegrityArguments.HashReader = $HashReader }
        if ($SignatureReader) { $IntegrityArguments.SignatureReader = $SignatureReader }
        $null = Test-ITPInstallerIntegrity @IntegrityArguments
        $InstallArguments = @(
            "/passive", "InstallAllUsers=0", "Include_launcher=1",
            "InstallLauncherAllUsers=0", "PrependPath=1", "Include_pip=1",
            "Include_test=0", "SimpleInstall=1"
        )
        $ExitCode = & $InstallerRunner $InstallerPath $InstallArguments
        if ($ExitCode -notin @(0, 3010)) {
            if ($ExitCode -in @(1260, 1625)) {
                throw "Python installer execution was blocked by AppLocker, WDAC, Group Policy, or endpoint security (exit code $ExitCode)."
            }
            throw "Python installer failed with exit code $ExitCode."
        }
        return [PSCustomObject]@{
            ExitCode = $ExitCode
            RestartRequired = $ExitCode -eq 3010
            Arguments = $InstallArguments
        }
    }
    finally {
        & $CleanupRunner $TemporaryDirectory
    }
}

function Initialize-ITPPython {
    param(
        [string[]]$Arguments,
        [scriptblock]$CommandResolver = {
            param($Name)
            Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
                Select-Object -First 1
        },
        [scriptblock]$Probe = {
            param($Executable, $Prefix)
            Invoke-ITPPythonProbe -Executable $Executable -PrefixArguments $Prefix
        },
        [scriptblock]$PipProbe = {
            param($Executable, $Prefix)
            Invoke-ITPPipProbe -Executable $Executable -PrefixArguments $Prefix
        },
        [scriptblock]$ConsentReader = {
            param($Provider)
            Read-Host "Install Python 3.12 using $Provider? [Y/n]"
        },
        [scriptblock]$WingetRunner = {
            param($Executable, $Arguments)
            & $Executable @Arguments | Out-Host
            return $LASTEXITCODE
        },
        [scriptblock]$DirectInstaller = {
            param($Metadata)
            Install-ITPDirectPython -Metadata $Metadata
        },
        [scriptblock]$PathReader = {
            param($Scope)
            [Environment]::GetEnvironmentVariable("Path", $Scope)
        },
        [string]$ArchitectureOverride,
        [Nullable[bool]]$InteractiveOverride = $null
    )

    $Discovery = Find-ITPPython -CommandResolver $CommandResolver -Probe $Probe
    if ($Discovery.Selected) {
        return $Discovery.Selected
    }
    if ($Discovery.Unsupported.Count -gt 0) {
        [Console]::Error.WriteLine(
            "ITP found unsupported Python: $($Discovery.Unsupported -join ', '). Python $script:ITPMinimumPython or later is required.")
    }
    else {
        [Console]::Error.WriteLine(
            "ITP requires Python $script:ITPMinimumPython or later, but no supported interpreter was found.")
    }

    if (-not (Test-ITPInteractiveDeployment -Arguments $Arguments -InteractiveOverride $InteractiveOverride)) {
        throw (
            "ITP will not download or install software without interactive consent. " +
            "This invocation is non-interactive. Install Python 3.9 or later for the current user, then rerun: " +
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy")
    }

    $WingetCommand = & $CommandResolver "winget"
    if ($null -ne $WingetCommand) {
        [Console]::Error.WriteLine(
            "ITP can install Python 3.12 using exact WinGet package $script:ITPPythonPackage.")
        $Consent = "$(& $ConsentReader 'WinGet')".Trim()
        if ($Consent -and $Consent -notmatch '^(?i:y|yes)$') {
            throw "Python installation was declined. No software was installed."
        }
        $InstallArguments = @(
            "install", "--id", $script:ITPPythonPackage, "--exact", "--source", "winget",
            "--accept-package-agreements", "--accept-source-agreements"
        )
        $InstallExitCode = & $WingetRunner $WingetCommand.Source $InstallArguments
        if ($InstallExitCode -eq 0) {
            Update-ITPProcessPath -PathReader $PathReader
            $Discovery = Find-ITPPython -IncludeInstallLocations `
                -CommandResolver $CommandResolver -Probe $Probe
            if ($Discovery.Selected -and (& $PipProbe `
                    $Discovery.Selected.Executable $Discovery.Selected.Prefix)) {
                return $Discovery.Selected
            }
            [Console]::Error.WriteLine(
                "WinGet completed, but Python or pip was not resolvable; trying the verified python.org installer.")
        }
        else {
            [Console]::Error.WriteLine(
                "WinGet installation failed with exit code $InstallExitCode; trying the verified python.org installer.")
        }
    }

    $Architecture = Get-ITPNativeArchitecture -ArchitectureOverride $ArchitectureOverride
    $Metadata = Get-ITPPythonInstaller -Architecture $Architecture
    [Console]::Error.WriteLine("Python is required to continue.")
    [Console]::Error.WriteLine(
        "ITP proposes CPython $($Metadata.Version) for $Architecture from www.python.org.")
    [Console]::Error.WriteLine(
        "The official installer will be downloaded, verified, and installed for the current user.")
    $Consent = "$(& $ConsentReader 'the verified www.python.org installer')".Trim()
    if ($Consent -and $Consent -notmatch '^(?i:y|yes)$') {
        throw "Python installation was declined. No software was installed."
    }

    $InstallResult = & $DirectInstaller $Metadata
    Update-ITPProcessPath -PathReader $PathReader
    $Discovery = Find-ITPPython -IncludeInstallLocations -Architecture $Architecture `
        -CommandResolver $CommandResolver -Probe $Probe
    if ($Discovery.Selected -and (& $PipProbe `
            $Discovery.Selected.Executable $Discovery.Selected.Prefix)) {
        if ($InstallResult.RestartRequired) {
            [Console]::Error.WriteLine(
                "Python installed successfully; Windows reported that a restart may be required.")
        }
        return $Discovery.Selected
    }
    $Checked = (Get-ITPPythonCandidates -IncludeInstallLocations -Architecture $Architecture |
        ForEach-Object { if ($_.Path) { $_.Path } else { $_.Name } }) -join ", "
    throw (
        "Python installation reported success, but a supported interpreter with pip was not found. " +
        "Locations checked: $Checked. Open a new PowerShell session and rerun: " +
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy")
}

function Test-ITPRunningOnWindows {
    param([string]$PlatformOverride)
    if ($PlatformOverride) {
        return $PlatformOverride -eq "Windows"
    }
    return $env:OS -eq "Windows_NT"
}

function Get-ITPOptionalFeatureState {
    param([string]$Name)
    if (-not (Get-Command Get-WindowsOptionalFeature -ErrorAction SilentlyContinue)) {
        return "Unknown"
    }
    try {
        $Feature = Get-WindowsOptionalFeature -Online -FeatureName $Name `
            -ErrorAction Stop
        return "$($Feature.State)"
    }
    catch {
        return "Unknown"
    }
}

function Test-ITPPendingReboot {
    if (-not (Test-ITPRunningOnWindows)) {
        return $false
    }
    $Keys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"
    )
    foreach ($Key in $Keys) {
        if (Test-Path -LiteralPath $Key) { return $true }
    }
    try {
        $Value = Get-ItemProperty `
            -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager" `
            -Name PendingFileRenameOperations -ErrorAction SilentlyContinue
        return $null -ne $Value.PendingFileRenameOperations
    }
    catch {
        return $false
    }
}

function Invoke-ITPCommandCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Arguments = @()
    )
    try {
        $Output = & $Executable @Arguments 2>&1 | Out-String
        return [PSCustomObject]@{
            ExitCode = $LASTEXITCODE
            Output = $Output.Trim()
        }
    }
    catch {
        return [PSCustomObject]@{
            ExitCode = 1
            Output = $_.Exception.Message
        }
    }
}

function Get-ITPWindowsPlatformState {
    param(
        [string]$PlatformOverride,
        [string]$ArchitectureOverride,
        [Nullable[bool]]$InteractiveOverride = $null,
        [scriptblock]$CommandResolver = {
            param($Name)
            Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
                Select-Object -First 1
        },
        [scriptblock]$CommandRunner = {
            param($Executable, $Arguments)
            Invoke-ITPCommandCapture -Executable $Executable -Arguments $Arguments
        },
        [scriptblock]$CimProvider = {
            param($ClassName)
            if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
                return Get-CimInstance $ClassName -ErrorAction SilentlyContinue
            }
            return $null
        },
        [scriptblock]$FeatureProvider = {
            param($Name)
            Get-ITPOptionalFeatureState $Name
        },
        [scriptblock]$RebootProvider = {
            Test-ITPPendingReboot
        },
        $EvidenceOverride
    )
    if (-not (Test-ITPRunningOnWindows -PlatformOverride $PlatformOverride)) {
        return [PSCustomObject]@{
            Applicable = $false
            Platform = [PSCustomObject]@{ Name = "Non-Windows" }
        }
    }
    if ($EvidenceOverride) {
        return $EvidenceOverride
    }

    $OperatingSystem = try {
        & $CimProvider "Win32_OperatingSystem"
    } catch { $null }
    $Processor = try {
        & $CimProvider "Win32_Processor" | Select-Object -First 1
    } catch { $null }
    $Computer = try {
        & $CimProvider "Win32_ComputerSystem"
    } catch { $null }
    $Architecture = try {
        Get-ITPNativeArchitecture -ArchitectureOverride $ArchitectureOverride
    } catch { "Unsupported" }
    $ProcessArchitecture = try {
        [System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString()
    } catch { $env:PROCESSOR_ARCHITECTURE }

    $WslFeature = & $FeatureProvider "Microsoft-Windows-Subsystem-Linux"
    $VirtualMachinePlatform = & $FeatureProvider "VirtualMachinePlatform"
    $HyperV = & $FeatureProvider "Microsoft-Hyper-V-All"
    $HypervisorPlatform = & $FeatureProvider "HypervisorPlatform"
    $Wsl = & $CommandResolver "wsl"
    $WslVersion = ""
    $WslKernel = ""
    $DefaultWslVersion = ""
    $WslStatusSuccessful = $false
    if ($Wsl) {
        $VersionResult = & $CommandRunner $Wsl.Source @("--version")
        if ($VersionResult.ExitCode -eq 0) {
            $VersionLine = $VersionResult.Output -split "`r?`n" |
                Where-Object { $_ -match '(?i)^WSL version:' } |
                Select-Object -First 1
            $KernelLine = $VersionResult.Output -split "`r?`n" |
                Where-Object { $_ -match '(?i)^Kernel version:' } |
                Select-Object -First 1
            $WslVersion = "$VersionLine".Split(":", 2)[-1].Trim()
            $WslKernel = "$KernelLine".Split(":", 2)[-1].Trim()
        }
        $StatusResult = & $CommandRunner $Wsl.Source @("--status")
        $WslStatusSuccessful = $StatusResult.ExitCode -eq 0
        $StatusOutput = "$($StatusResult.Output)".Replace("`0", "")
        $DefaultLine = $StatusOutput -split "`r?`n" |
            Where-Object { $_ -match '(?i)^Default Version:' } |
            Select-Object -First 1
        if ($DefaultLine) {
            $DefaultWslVersion = "$DefaultLine".Split(":", 2)[-1].Trim()
        }
        if ($StatusResult.ExitCode -eq 0 -and $WslFeature -eq "Unknown") {
            $WslFeature = "Enabled"
        }
        if ($StatusResult.ExitCode -eq 0 -and $DefaultWslVersion -eq "2" -and
                $VirtualMachinePlatform -eq "Unknown") {
            $VirtualMachinePlatform = "Enabled"
        }
    }

    $DockerDesktopPaths = @()
    if ($env:ProgramW6432) {
        $DockerDesktopPaths += Join-Path $env:ProgramW6432 `
            "Docker\Docker\Docker Desktop.exe"
    }
    if ($env:ProgramFiles) {
        $DockerDesktopPaths += Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    }
    if ($env:LOCALAPPDATA) {
        $DockerDesktopPaths += Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe"
    }
    $DockerDesktopPath = $DockerDesktopPaths |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
    $DockerDesktopRegistry = $false
    foreach ($RegistryPath in @(
            "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
            "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*")) {
        try {
            if (Get-ItemProperty $RegistryPath -ErrorAction SilentlyContinue |
                    Where-Object { $_.DisplayName -eq "Docker Desktop" } |
                    Select-Object -First 1) {
                $DockerDesktopRegistry = $true
                break
            }
        }
        catch {
            # A missing registry view is normal.
        }
    }
    $DockerDesktopRunning = $false
    try {
        $DockerDesktopRunning = $null -ne (
            Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue |
                Select-Object -First 1)
    }
    catch {
        $DockerDesktopRunning = $false
    }
    $Docker = & $CommandResolver "docker"
    $DockerCliAvailable = $null -ne $Docker
    $DockerDaemon = $false
    $Compose = $false
    $DockerBackend = "Unknown"
    $DockerVersion = ""
    $DockerArchitecture = ""
    $DockerKernel = ""
    if ($Docker) {
        $VersionResult = & $CommandRunner $Docker.Source @("--version")
        $DockerVersion = $VersionResult.Output
        $InfoResult = & $CommandRunner $Docker.Source @("info")
        $DockerDaemon = $InfoResult.ExitCode -eq 0
        if ($InfoResult.Output -match '(?i)wsl') { $DockerBackend = "WSL" }
        elseif ($InfoResult.Output -match '(?i)hyper-v') { $DockerBackend = "Hyper-V" }
        $ArchitectureLine = $InfoResult.Output -split "`r?`n" |
            Where-Object { $_ -match '(?i)^\s*Architecture\s*:' } |
            Select-Object -First 1
        if ($ArchitectureLine) {
            $DockerArchitecture = "$ArchitectureLine".Split(":", 2)[-1].Trim()
        }
        $KernelLine = $InfoResult.Output -split "`r?`n" |
            Where-Object { $_ -match '(?i)^\s*Kernel Version\s*:' } |
            Select-Object -First 1
        if ($KernelLine) {
            $DockerKernel = "$KernelLine".Split(":", 2)[-1].Trim()
        }
        $ComposeResult = & $CommandRunner $Docker.Source @("compose", "version")
        $Compose = $ComposeResult.ExitCode -eq 0
    }
    $SystemInfoOutput = ""
    $SystemInfo = & $CommandResolver "systeminfo"
    if ($SystemInfo) {
        $SystemInfoResult = & $CommandRunner $SystemInfo.Source @()
        if ($SystemInfoResult.ExitCode -eq 0) {
            $SystemInfoOutput = "$($SystemInfoResult.Output)"
        }
    }
    $SystemInfoHypervisor = (
        $SystemInfoOutput -match '(?i)a hypervisor has been detected')
    $VbsRunning = (
        $SystemInfoOutput -match
            '(?is)virtualization-based security[^\r\n]*running')
    $SystemInfoFirmwareEnabled = $null
    if ($SystemInfoOutput -match
            '(?i)virtualization enabled in firmware\s*:\s*(yes|no)') {
        $SystemInfoFirmwareEnabled = $Matches[1].ToLowerInvariant() -eq "yes"
    }
    $RawVmMonitor = if ($Processor -and
            $null -ne $Processor.VMMonitorModeExtensions) {
        [bool]$Processor.VMMonitorModeExtensions
    } else { $null }
    $RawSlat = if ($Processor -and
            $null -ne $Processor.SecondLevelAddressTranslationExtensions) {
        [bool]$Processor.SecondLevelAddressTranslationExtensions
    } else { $null }
    $RawFirmware = if ($Processor -and
            $null -ne $Processor.VirtualizationFirmwareEnabled) {
        [bool]$Processor.VirtualizationFirmwareEnabled
    } else { $null }
    $HypervisorPresent = if ($Computer -and
            $null -ne $Computer.HypervisorPresent) {
        [bool]$Computer.HypervisorPresent
    } else { $null }
    $Wsl2Operational = (
        $WslStatusSuccessful -and $DefaultWslVersion -eq "2")
    $DockerVirtualizationOperational = $DockerDaemon
    $OperationalEvidence = @()
    if ($DockerDaemon) { $OperationalEvidence += "docker.daemon_reachable" }
    if ($DockerVirtualizationOperational) {
        $OperationalEvidence += "docker.virtualization_backend_operational"
    }
    if ($Wsl2Operational) { $OperationalEvidence += "wsl2.operational" }
    if ($HypervisorPresent -eq $true) {
        $OperationalEvidence += "windows.hypervisor_present"
    }
    if ($SystemInfoHypervisor) {
        $OperationalEvidence += "systeminfo.hypervisor_detected"
    }
    if ($VbsRunning) { $OperationalEvidence += "windows.vbs_running" }
    $OperationalEvidence = @($OperationalEvidence | Sort-Object -Unique)
    $HasOperationalEvidence = $OperationalEvidence.Count -gt 0
    $FirmwareEvidenceReliable = (
        $null -ne $SystemInfoFirmwareEnabled -or
        ($Architecture -in @("AMD64", "x86") -and $null -ne $RawFirmware))
    $FirmwareState = "unknown"
    if ($HasOperationalEvidence -or $RawFirmware -eq $true -or
            $SystemInfoFirmwareEnabled -eq $true) {
        $FirmwareState = "enabled"
    }
    elseif ($FirmwareEvidenceReliable -and (
            $RawFirmware -eq $false -or
            $SystemInfoFirmwareEnabled -eq $false)) {
        $FirmwareState = "disabled"
    }
    $ConflictingEvidence = @()
    if ($HasOperationalEvidence -and $RawFirmware -eq $false) {
        $ConflictingEvidence += "cim.firmware_false_but_operational"
    }
    if ($Architecture -eq "ARM64" -and $RawSlat -eq $false) {
        $ConflictingEvidence += "cim.slat_false_not_authoritative_on_arm64"
    }
    if ($SystemInfoFirmwareEnabled -eq $false -and $HasOperationalEvidence) {
        $ConflictingEvidence += "systeminfo.firmware_false_but_operational"
    }
    $CpuCapable = if ($HasOperationalEvidence) {
        $true
    }
    elseif ($Architecture -eq "ARM64" -and $RawVmMonitor -eq $true) {
        $true
    }
    elseif ($Architecture -in @("AMD64", "x86") -and (
            $RawVmMonitor -eq $true -or $RawSlat -eq $true)) {
        $true
    }
    elseif ($FirmwareState -eq "disabled") {
        $false
    }
    else {
        $null
    }
    $Interactive = Test-ITPInteractiveDeployment -Arguments @("deploy") `
        -InteractiveOverride $InteractiveOverride
    $Caption = if ($OperatingSystem) { "$($OperatingSystem.Caption)" } else { "Windows" }
    $Version = if ($OperatingSystem) { "$($OperatingSystem.Version)" } else { "" }
    $Build = if ($OperatingSystem) { "$($OperatingSystem.BuildNumber)" } else { "" }
    $Edition = if ($OperatingSystem -and $OperatingSystem.OperatingSystemSKU) {
        "$($OperatingSystem.OperatingSystemSKU)"
    } else { $Caption }

    return [PSCustomObject]@{
        Applicable = $true
        Platform = [PSCustomObject]@{
            Name = $Caption
            Edition = $Edition
            Version = $Version
            Build = $Build
            LTSC = $Caption -match '(?i)LTSC|Long-Term Servicing'
            NativeArchitecture = $Architecture
            ProcessArchitecture = $ProcessArchitecture
            Supported = ($Version -match '^10\.' -and [int]$Build -ge 19041 -and
                $Caption -notmatch '(?i)Windows Server' -and
                $Architecture -in @("AMD64", "ARM64"))
        }
        Virtualization = [PSCustomObject]@{
            CpuCapable = $CpuCapable
            FirmwareEnabled = $(if ($FirmwareState -eq "enabled") {
                $true
            } elseif ($FirmwareState -eq "disabled") {
                $false
            } else {
                $null
            })
            nativeArchitecture = $Architecture
            firmwareVirtualizationState = $FirmwareState
            firmwareVirtualizationRaw = $RawFirmware
            firmwareEvidenceReliable = $FirmwareEvidenceReliable
            HyperVAvailable = $HyperV -eq "Enabled"
            HypervisorPresent = $HypervisorPresent
            wsl2Operational = $Wsl2Operational
            dockerVirtualizationOperational = $DockerVirtualizationOperational
            vbsRunning = $VbsRunning
            operationalEvidence = $OperationalEvidence
            conflictingEvidence = @($ConflictingEvidence | Sort-Object -Unique)
        }
        WindowsFeatures = [PSCustomObject]@{
            WSL = $WslFeature
            WSLVersion = $WslVersion
            WSLKernelVersion = $WslKernel
            DefaultWSLVersion = $DefaultWslVersion
            VirtualMachinePlatform = $VirtualMachinePlatform
            HyperV = $HyperV
            WindowsHypervisorPlatform = $HypervisorPlatform
        }
        Docker = [PSCustomObject]@{
            DesktopInstalled = ($null -ne $DockerDesktopPath -or
                $DockerDesktopRegistry)
            DesktopRunning = $DockerDesktopRunning
            DesktopPath = "$DockerDesktopPath"
            CliAvailable = $DockerCliAvailable
            Version = $DockerVersion
            Architecture = $DockerArchitecture
            Kernel = $DockerKernel
            DaemonReachable = $DockerDaemon
            ComposeV2 = $Compose
            Backend = $DockerBackend
        }
        RebootRequired = & $RebootProvider
        Interactive = $Interactive
    }
}

function Get-ITPWindowsReadiness {
    param([Parameter(Mandatory = $true)]$State)
    if (-not $State.Applicable) {
        return [PSCustomObject]@{
            RepairableItems = @()
            BlockingItems = @()
            Ready = $false
        }
    }
    $Repairable = @()
    $Blocking = @()
    if ($State.WindowsFeatures.WSL -ne "Enabled") {
        $Repairable += "windows_feature.wsl"
    }
    if ($State.WindowsFeatures.VirtualMachinePlatform -ne "Enabled") {
        $Repairable += "windows_feature.virtual_machine_platform"
    }
    if (-not $State.Platform.Supported) {
        $Blocking += "platform.unsupported"
    }
    if ($State.Virtualization.CpuCapable -eq $false) {
        $Blocking += "virtualization.cpu_unavailable"
    }
    if ($State.Virtualization.FirmwareEnabled -eq $false) {
        $Blocking += "virtualization.firmware_disabled"
    }
    if ($State.RebootRequired) {
        $Blocking += "system.restart_required"
    }
    if (-not $State.Docker.DesktopInstalled) {
        $Blocking += "docker.desktop_missing"
    }
    elseif (-not $State.Docker.CliAvailable) {
        $Blocking += "docker.cli_unavailable"
    }
    elseif (-not $State.Docker.DaemonReachable) {
        $Blocking += $(if ($State.Docker.DesktopRunning -eq $false) {
            "docker.desktop_stopped"
        } else {
            "docker.daemon_unavailable"
        })
    }
    elseif (-not $State.Docker.ComposeV2) {
        $Blocking += "docker.compose_v2_unavailable"
    }
    return [PSCustomObject]@{
        RepairableItems = $Repairable
        BlockingItems = $Blocking
        Ready = $Repairable.Count -eq 0 -and $Blocking.Count -eq 0
    }
}

function Resolve-ITPWindowsCanonicalPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Value = $Path.Replace("/", "\")
    if ($Value -notmatch '^(?<drive>[A-Za-z]):\\(?<tail>.*)$') {
        throw "Windows repository path must be an absolute drive path: $Path"
    }
    $Parts = New-Object System.Collections.Generic.List[string]
    foreach ($Part in ($Matches.tail -split '\\+')) {
        if (-not $Part -or $Part -eq ".") { continue }
        if ($Part -eq "..") {
            if ($Parts.Count -gt 0) { $Parts.RemoveAt($Parts.Count - 1) }
            continue
        }
        $Parts.Add($Part)
    }
    $Suffix = $Parts -join "\"
    return ("{0}:\{1}" -f $Matches.drive.ToUpperInvariant(), $Suffix).TrimEnd("\")
}

function Test-ITPWindowsRepositoryLocation {
    param([Parameter(Mandatory = $true)][string]$RepositoryPath)
    $Resolved = Resolve-ITPWindowsCanonicalPath $RepositoryPath
    $Protected = @(
        "C:\Windows",
        "C:\Program Files",
        "C:\Program Files (x86)",
        "C:\ProgramData"
    )
    foreach ($EnvironmentPath in @(
            $env:windir, $env:SystemRoot, $env:ProgramFiles,
            ${env:ProgramFiles(x86)}, $env:ProgramData)) {
        if ($EnvironmentPath -and $EnvironmentPath -match '^[A-Za-z]:[\\/]') {
            $Protected += Resolve-ITPWindowsCanonicalPath $EnvironmentPath
        }
    }
    foreach ($RootPath in ($Protected | Sort-Object -Unique)) {
        $CanonicalRoot = Resolve-ITPWindowsCanonicalPath $RootPath
        if ($Resolved.Equals(
                $CanonicalRoot, [StringComparison]::OrdinalIgnoreCase) -or
                $Resolved.StartsWith(
                    $CanonicalRoot + "\", [StringComparison]::OrdinalIgnoreCase)) {
            throw (
                "ITP cannot run from protected Windows location: $Resolved. " +
                "This location is unsuitable for generated configuration and " +
                "container bind mounts. Move or reclone the repository to C:\ITP " +
                "or a user-owned directory, then rerun: powershell.exe -NoProfile " +
                "-ExecutionPolicy Bypass -File .\itp.ps1 deploy")
        }
    }
    $Warning = (
        $Resolved -match '(?i)\\(Downloads|Desktop|OneDrive)(\\|$)' -or
        $Resolved -match '(?i)\\AppData\\Local\\Temp(\\|$)')
    return [PSCustomObject]@{
        Path = $Resolved
        Warning = $Warning
    }
}

function Initialize-ITPWindowsPlatform {
    param(
        [string[]]$Arguments,
        [string]$PlatformOverride,
        [scriptblock]$StateProvider = {
            Get-ITPWindowsPlatformState
        },
        [scriptblock]$ConsentReader = {
            Read-Host "Continue? [Y/n]"
        },
        [scriptblock]$FeatureRunner = {
            $Wsl = Get-Command wsl.exe -CommandType Application `
                -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $Wsl) {
                throw (
                    "WSL tooling is unavailable. Enable Windows Subsystem for Linux " +
                    "and Virtual Machine Platform through Windows Features, restart, " +
                    "then rerun the deployment.")
            }
            try {
                $Process = Start-Process -FilePath $Wsl.Source `
                    -ArgumentList @("--install", "--no-distribution") `
                    -Verb RunAs -PassThru -Wait
                return $Process.ExitCode
            }
            catch {
                if ($_.Exception.Message -match '(?i)canceled|cancelled') {
                    throw "Administrator approval was declined. No Windows features were changed."
                }
                throw (
                    "Windows feature enablement was blocked. Run an elevated PowerShell " +
                    "session or ask an administrator to review Group Policy: " +
                    $_.Exception.Message)
            }
        },
        [Nullable[bool]]$InteractiveOverride = $null,
        [string]$RepositoryPath
    )
    if ($Arguments.Count -eq 0 -or $Arguments[0] -ne "deploy" -or
            -not (Test-ITPRunningOnWindows -PlatformOverride $PlatformOverride)) {
        return [PSCustomObject]@{
            Continue = $true
            RestartRequired = $false
            ExitCode = 0
        }
    }
    if (-not $RepositoryPath) {
        $RepositoryPath = if ($PlatformOverride) {
            "C:\ITP"
        } else {
            Split-Path $PSScriptRoot -Parent
        }
    }
    $Location = Test-ITPWindowsRepositoryLocation -RepositoryPath $RepositoryPath
    if ($Location.Warning) {
        [Console]::Error.WriteLine(
            "WARNING: Repository location may be unsuitable for a durable deployment: " +
            $Location.Path)
        [Console]::Error.WriteLine(
            "Prefer C:\ITP or another user-owned, non-synchronised directory.")
    }
    $State = & $StateProvider
    $Readiness = Get-ITPWindowsReadiness -State $State
    if (-not $State.Platform.Supported) {
        throw (
            "This Windows version, edition, build, or architecture is unsupported. " +
            "ITP requires a supported Windows 10 build 19041 or later, or Windows 11; " +
            "Windows Server is not supported by this Docker Desktop workflow.")
    }
    if ($State.Virtualization.CpuCapable -eq $false) {
        throw (
            "This processor does not report the virtualization capabilities required " +
            "for WSL 2. No Windows features were changed.")
    }
    if ($State.Virtualization.FirmwareEnabled -eq $false) {
        throw (
            "Hardware virtualisation is confirmed disabled. Enable hardware " +
            "virtualisation in UEFI/firmware, restart Windows, and rerun the deployment.")
    }
    if ($State.RebootRequired) {
        [Console]::Error.WriteLine(
            "Windows reports a pending restart. Restart before Docker validation, then rerun:")
        [Console]::Error.WriteLine(
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy")
        return [PSCustomObject]@{
            Continue = $false
            RestartRequired = $true
            ExitCode = 3010
        }
    }
    if ($Readiness.RepairableItems.Count -gt 0) {
        $Interactive = Test-ITPInteractiveDeployment -Arguments $Arguments `
            -InteractiveOverride $InteractiveOverride
        if (-not $Interactive) {
            throw (
                "Windows platform preparation requires explicit interactive consent. " +
                "Enable Windows Subsystem for Linux and Virtual Machine Platform, " +
                "restart Windows, then rerun the deployment.")
        }
        [Console]::Error.WriteLine("Windows platform preparation")
        [Console]::Error.WriteLine("")
        [Console]::Error.WriteLine(
            "The following Windows features are required before Docker Desktop can run:")
        foreach ($Item in $Readiness.RepairableItems) {
            $Label = switch ($Item) {
                "windows_feature.wsl" { "Windows Subsystem for Linux" }
                "windows_feature.virtual_machine_platform" { "Virtual Machine Platform" }
                default { $Item }
            }
            [Console]::Error.WriteLine("- $Label")
        }
        [Console]::Error.WriteLine("")
        [Console]::Error.WriteLine(
            "These changes require administrator approval and a Windows restart.")
        $Consent = "$(& $ConsentReader)".Trim()
        if ($Consent -and $Consent -notmatch '^(?i:y|yes)$') {
            throw "Windows platform preparation was declined. No Windows features were changed."
        }
        $ExitCode = & $FeatureRunner
        if ($ExitCode -notin @(0, 3010)) {
            throw "Windows platform preparation failed with exit code $ExitCode."
        }
        [Console]::Error.WriteLine("")
        [Console]::Error.WriteLine(
            "Windows platform preparation completed successfully.")
        [Console]::Error.WriteLine(
            "A restart is required before Docker Desktop can run.")
        [Console]::Error.WriteLine("After restarting, rerun:")
        [Console]::Error.WriteLine(
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\itp.ps1 deploy")
        return [PSCustomObject]@{
            Continue = $false
            RestartRequired = $true
            ExitCode = 3010
        }
    }
    if (-not $State.Docker.DesktopInstalled) {
        throw (
            "Docker Desktop is not installed. Install Docker Desktop using Linux " +
            "containers, then rerun: powershell.exe -NoProfile -ExecutionPolicy " +
            "Bypass -File .\itp.ps1 deploy")
    }
    if (-not $State.Docker.CliAvailable) {
        throw (
            "Docker Desktop is installed, but the Docker CLI is unavailable. " +
            "Repair Docker Desktop or its PATH integration, then rerun the deployment.")
    }
    if (-not $State.Docker.DaemonReachable) {
        if ($State.Docker.DesktopRunning -eq $false) {
            throw (
                "Docker Desktop is installed but not running. Start Docker Desktop, " +
                "wait for the Linux container engine, then rerun the deployment.")
        }
        throw (
            "Docker Desktop is running, but the Docker daemon is unavailable. " +
            "Check the Docker Desktop engine status and selected Linux-container " +
            "backend, then rerun the deployment.")
    }
    if (-not $State.Docker.ComposeV2) {
        throw (
            "Docker Compose v2 is unavailable. Update or repair Docker Desktop, " +
            "then rerun the deployment.")
    }
    return [PSCustomObject]@{
        Continue = $true
        RestartRequired = $false
        ExitCode = 0
    }
}

function New-ITPPrerequisiteCheck {
    param($State, $Name, $Detail, $Classification)
    [PSCustomObject]@{
        State = $State
        Name = $Name
        Detail = $Detail
        Classification = $Classification
    }
}

function Get-ITPPrerequisiteDiagnostics {
    param(
        [string]$PlatformOverride,
        [string]$ArchitectureOverride,
        [Nullable[bool]]$InteractiveOverride = $null,
        [scriptblock]$PlatformStateProvider = {
            param($Platform, $Architecture, $Interactive)
            Get-ITPWindowsPlatformState -PlatformOverride $Platform `
                -ArchitectureOverride $Architecture `
                -InteractiveOverride $Interactive
        },
        [scriptblock]$CommandResolver = {
            param($Name)
            Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
                Select-Object -First 1
        },
        [scriptblock]$Probe = {
            param($Executable, $Prefix)
            Invoke-ITPPythonProbe -Executable $Executable -PrefixArguments $Prefix
        },
        [scriptblock]$ReachabilityProbe = {
            param($Url)
            try {
                $Request = [Net.HttpWebRequest]::Create($Url)
                $Request.Method = "HEAD"
                $Request.AllowAutoRedirect = $false
                $Request.Timeout = 15000
                $Response = $Request.GetResponse()
                $Status = [int]$Response.StatusCode
                $Response.Close()
                return [PSCustomObject]@{ Success = ($Status -ge 200 -and $Status -lt 400); Detail = "HTTP $Status" }
            }
            catch {
                return [PSCustomObject]@{ Success = $false; Detail = $_.Exception.Message }
            }
        }
    )
    $Checks = @()
    $RunningOnWindows = if ($PlatformOverride) {
        $PlatformOverride -eq "Windows"
    } else {
        $env:OS -eq "Windows_NT"
    }
    if (-not $RunningOnWindows) {
        $Checks += New-ITPPrerequisiteCheck "INFO" "Platform" `
            "Windows diagnostics are running on a non-Windows host; no system changes will be made." "optional"
        return [PSCustomObject]@{
            Checks = $Checks
            ExitCode = 0
            Ready = $false
            Applicable = $false
            platform = [PSCustomObject]@{ Name = "Non-Windows" }
            windowsFeatures = $null
            virtualization = $null
            docker = $null
            rebootRequired = $false
            repairableItems = @()
            blockingItems = @()
            interactive = $false
        }
    }

    $PlatformState = & $PlatformStateProvider `
        $PlatformOverride $ArchitectureOverride $InteractiveOverride
    $PlatformReadiness = Get-ITPWindowsReadiness -State $PlatformState
    $OsCaption = if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) {
        Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    } else { $null }
    $OsDetail = if ($OsCaption) {
        "$($OsCaption.Caption) version $($OsCaption.Version) build $($OsCaption.BuildNumber)"
    } else { [Environment]::OSVersion.VersionString }
    $Checks += New-ITPPrerequisiteCheck "INFO" "Operating system" $OsDetail "required"
    try {
        $Architecture = Get-ITPNativeArchitecture -ArchitectureOverride $ArchitectureOverride
        $Checks += New-ITPPrerequisiteCheck "PASS" "Native architecture" $Architecture "required"
    }
    catch {
        $Architecture = $null
        $Checks += New-ITPPrerequisiteCheck "FAIL" "Native architecture" $_.Exception.Message "blocking"
    }
    $Checks += New-ITPPrerequisiteCheck "INFO" "Process architecture" `
        ([System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString()) "required"
    $Checks += New-ITPPrerequisiteCheck "INFO" "PowerShell" `
        "$($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion)" "required"
    $Policies = try {
        (Get-ExecutionPolicy -List | ForEach-Object { "$($_.Scope)=$($_.ExecutionPolicy)" }) -join "; "
    } catch { "Unavailable: $($_.Exception.Message)" }
    $Checks += New-ITPPrerequisiteCheck "INFO" "Execution policy" $Policies "optional"

    $WingetAvailable = $false
    foreach ($Tool in @(
            @{ Name = "git"; Label = "Git"; Arguments = @("--version"); Required = $false; Classification = "required before launcher execution" },
            @{ Name = "docker"; Label = "Docker CLI"; Arguments = @("--version"); Required = $true },
            @{ Name = "winget"; Label = "WinGet"; Arguments = @("--version"); Required = $false })) {
        $Command = & $CommandResolver $Tool.Name
        if ($Command) {
            if ($Tool.Name -eq "winget") { $WingetAvailable = $true }
            $Version = try { (& $Command.Source @($Tool.Arguments) 2>&1 | Select-Object -First 1) } catch { "version unavailable" }
            $Checks += New-ITPPrerequisiteCheck "PASS" $Tool.Label "$($Command.Source): $Version" `
                $(if ($Tool.Classification) { $Tool.Classification } elseif ($Tool.Required) { "required" } else { "automated provider" })
        } else {
            $Checks += New-ITPPrerequisiteCheck $(if ($Tool.Required) { "FAIL" } elseif ($Tool.Classification) { "WARNING" } else { "INFO" }) `
                $Tool.Label "Not found" $(if ($Tool.Required) { "blocking" } elseif ($Tool.Classification) { $Tool.Classification } else { "optional" })
        }
    }
    $Docker = & $CommandResolver "docker"
    if ($Docker) {
        & $Docker.Source info *> $null
        $Checks += New-ITPPrerequisiteCheck $(if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }) `
            "Docker daemon" $(if ($LASTEXITCODE -eq 0) { "Reachable" } else { "Unavailable" }) "blocking"
        & $Docker.Source compose version *> $null
        $Checks += New-ITPPrerequisiteCheck $(if ($LASTEXITCODE -eq 0) { "PASS" } else { "FAIL" }) `
            "Docker Compose v2" $(if ($LASTEXITCODE -eq 0) { "Available" } else { "Unavailable" }) "blocking"
    } else {
        $Checks += New-ITPPrerequisiteCheck "FAIL" "Docker daemon" `
            "Cannot be checked because Docker CLI is unavailable" "blocking"
        $Checks += New-ITPPrerequisiteCheck "FAIL" "Docker Compose v2" `
            "Cannot be checked because Docker CLI is unavailable" "blocking"
    }
    $Python = Find-ITPPython -IncludeInstallLocations -Architecture $Architecture `
        -CommandResolver $CommandResolver -Probe $Probe
    if ($Python.Selected) {
        $Checks += New-ITPPrerequisiteCheck "PASS" "Python" `
            "$($Python.Selected.Version) at $($Python.Selected.Executable)" "required"
        $Pip = Invoke-ITPPipProbe -Executable $Python.Selected.Executable `
            -PrefixArguments $Python.Selected.Prefix
        $Checks += New-ITPPrerequisiteCheck $(if ($Pip) { "PASS" } else { "FAIL" }) `
            "pip" $(if ($Pip) { "Available" } else { "Unavailable" }) "blocking"
    } else {
        $Interactive = Test-ITPInteractiveDeployment -Arguments @("deploy") `
            -InteractiveOverride $InteractiveOverride
        $Repairable = $Interactive -and $null -ne $Architecture
        $Checks += New-ITPPrerequisiteCheck $(if ($Repairable) { "WARNING" } else { "FAIL" }) `
            "Python" $(if ($Repairable) { "Missing; automatically repairable with consent" } else { "Missing" }) `
            $(if ($Repairable) { "automatically repairable" } else { "blocking" })
        $Checks += New-ITPPrerequisiteCheck "INFO" "pip" `
            "Cannot be checked until Python is available" "automatically repairable"
    }
    $Launcher = & $CommandResolver "py"
    $Checks += New-ITPPrerequisiteCheck $(if ($Launcher) { "PASS" } else { "INFO" }) `
        "Python launcher" $(if ($Launcher) { $Launcher.Source } else { "Not found" }) "optional"
    $DesktopInstaller = try {
        Get-AppxPackage -Name Microsoft.DesktopAppInstaller -ErrorAction SilentlyContinue
    } catch { $null }
    $Checks += New-ITPPrerequisiteCheck $(if ($DesktopInstaller) { "PASS" } else { "INFO" }) `
        "Microsoft Desktop App Installer" $(if ($DesktopInstaller) { "Present" } else { "Not present" }) "optional"
    if ($Architecture) {
        $Metadata = Get-ITPPythonInstaller -Architecture $Architecture
        $Checks += New-ITPPrerequisiteCheck "PASS" "Direct python.org bootstrap" `
            "CPython $($Metadata.Version) $Architecture is pinned and verified" "automatically repairable"
        $Reachability = & $ReachabilityProbe $Metadata.Url
        $EndpointBlocking = (
            -not $Reachability.Success -and -not $Python.Selected -and
            -not $WingetAvailable)
        $Checks += New-ITPPrerequisiteCheck $(if ($Reachability.Success) { "PASS" } elseif ($EndpointBlocking) { "FAIL" } else { "WARNING" }) `
            "python.org endpoint" "$($Reachability.Detail): $($Metadata.Url)" `
            $(if ($EndpointBlocking) { "blocking" } else { "automated provider" })
    }
    $InteractiveState = Test-ITPInteractiveDeployment -Arguments @("deploy") `
        -InteractiveOverride $InteractiveOverride
    $Checks += New-ITPPrerequisiteCheck "INFO" "Interaction" `
        $(if ($InteractiveState) { "Interactive consent is available" } else { "Non-interactive; automatic software installation is disabled" }) "required"
    $CheckBlocking = @($Checks |
        Where-Object { $_.State -eq "FAIL" -and $_.Classification -eq "blocking" } |
        ForEach-Object {
            switch ($_.Name) {
                "Native architecture" { "platform.architecture_unsupported" }
                "Docker CLI" { "docker.cli_unavailable" }
                "Docker daemon" { "docker.daemon_unavailable" }
                "Docker Compose v2" { "docker.compose_v2_unavailable" }
                "Python" { "python.unavailable" }
                "pip" { "python.pip_unavailable" }
                "python.org endpoint" { "python.download_unavailable" }
                default { "prerequisite.$(($_.Name -replace '[^A-Za-z0-9]+', '_').Trim('_').ToLowerInvariant())" }
            }
        })
    if (-not $InteractiveState -and $PlatformReadiness.RepairableItems.Count -gt 0) {
        $CheckBlocking += "interaction.required_for_windows_preparation"
    }
    $Blocking = @($CheckBlocking + $PlatformReadiness.BlockingItems |
        Sort-Object -Unique)
    return [PSCustomObject]@{
        Checks = $Checks
        ExitCode = $(if ($Blocking.Count -eq 0) { 0 } else { 1 })
        Ready = $Blocking.Count -eq 0
        Applicable = $true
        platform = $PlatformState.Platform
        windowsFeatures = $PlatformState.WindowsFeatures
        virtualization = $PlatformState.Virtualization
        docker = $PlatformState.Docker
        rebootRequired = [bool]$PlatformState.RebootRequired
        repairableItems = @($PlatformReadiness.RepairableItems)
        blockingItems = @($Blocking)
        interactive = [bool]$PlatformState.Interactive
    }
}

function Write-ITPPrerequisiteSection {
    param([string]$Name)
    Write-Output "=================================================="
    Write-Output $Name
    Write-Output "=================================================="
}

function Write-ITPPrerequisiteValue {
    param([string]$State, [string]$Name, $Value)
    Write-Output ("{0} {1}: {2}" -f $State, $Name, $Value)
}

function Get-ITPPrerequisiteItemLabel {
    param([string]$Identifier)
    switch ($Identifier) {
        "windows_feature.wsl" { return "Windows Subsystem for Linux" }
        "windows_feature.virtual_machine_platform" { return "Virtual Machine Platform" }
        "platform.unsupported" { return "Unsupported Windows version, edition, build, or architecture" }
        "virtualization.cpu_unavailable" { return "CPU virtualization capability is unavailable" }
        "virtualization.firmware_disabled" { return "Virtualization is disabled in firmware" }
        "system.restart_required" { return "Windows restart required" }
        "docker.desktop_missing" { return "Docker Desktop is not installed" }
        "docker.cli_unavailable" { return "Docker CLI is unavailable" }
        "docker.desktop_stopped" { return "Docker Desktop is installed but stopped" }
        "docker.daemon_unavailable" { return "Docker daemon is unavailable" }
        "docker.compose_v2_unavailable" { return "Docker Compose v2 is unavailable" }
        "interaction.required_for_windows_preparation" {
            return "Interactive consent is required for Windows preparation"
        }
        default { return $Identifier }
    }
}

function Invoke-ITPPrerequisiteDiagnostics {
    param(
        [Nullable[bool]]$InteractiveOverride = $null,
        [switch]$Json
    )
    $Result = Get-ITPPrerequisiteDiagnostics -InteractiveOverride $InteractiveOverride
    if ($Json) {
        Write-Output ($Result | ConvertTo-Json -Depth 8)
        $script:ITPPrerequisiteExitCode = $Result.ExitCode
        return
    }
    Write-Output "ITP Windows prerequisite diagnostics"
    if (-not $Result.Applicable) {
        foreach ($Check in $Result.Checks) {
            Write-Output ("[{0}] {1} - {2} ({3})" -f `
                $Check.State, $Check.Name, $Check.Detail, $Check.Classification)
        }
    } else {
        Write-ITPPrerequisiteSection "Platform"
        Write-ITPPrerequisiteValue $(if ($Result.platform.Supported) { "PASS" } else { "FAIL" }) `
            "Windows" "$($Result.platform.Name) version $($Result.platform.Version) build $($Result.platform.Build)"
        Write-ITPPrerequisiteValue "INFO" "Edition" $Result.platform.Edition
        Write-ITPPrerequisiteValue "INFO" "LTSC" $Result.platform.LTSC
        Write-ITPPrerequisiteValue "PASS" "Native architecture" $Result.platform.NativeArchitecture
        Write-ITPPrerequisiteValue "INFO" "Process architecture" $Result.platform.ProcessArchitecture
        Write-Output ""

        Write-ITPPrerequisiteSection "Virtualization"
        $FirmwareDisplayState = if (
                $Result.virtualization.PSObject.Properties.Name -contains
                    "FirmwareVirtualizationState") {
            "$($Result.virtualization.FirmwareVirtualizationState)"
        } elseif ($Result.virtualization.FirmwareEnabled -eq $true) {
            "enabled"
        } elseif ($Result.virtualization.FirmwareEnabled -eq $false) {
            "disabled"
        } else {
            "unknown"
        }
        Write-ITPPrerequisiteValue $(if ($Result.virtualization.CpuCapable -eq $false) { "FAIL" } elseif ($null -eq $Result.virtualization.CpuCapable) { "INFO" } else { "PASS" }) `
            "Hardware virtualisation operational" $Result.virtualization.CpuCapable
        Write-ITPPrerequisiteValue $(if ($FirmwareDisplayState -eq "disabled") { "FAIL" } elseif ($FirmwareDisplayState -eq "unknown") { "WARNING" } else { "PASS" }) `
            "Firmware virtualisation state" $FirmwareDisplayState
        Write-ITPPrerequisiteValue "INFO" "Hyper-V available" $Result.virtualization.HyperVAvailable
        Write-ITPPrerequisiteValue $(if ($Result.virtualization.HypervisorPresent) { "PASS" } else { "INFO" }) `
            "Active Windows hypervisor detected" `
            $Result.virtualization.HypervisorPresent
        Write-ITPPrerequisiteValue $(if ($Result.virtualization.WSL2Operational) { "PASS" } else { "INFO" }) `
            "WSL2 operational" $Result.virtualization.WSL2Operational
        Write-ITPPrerequisiteValue $(if ($Result.virtualization.DockerVirtualizationOperational) { "PASS" } else { "INFO" }) `
            "Docker Desktop virtualisation backend operational" `
            $Result.virtualization.DockerVirtualizationOperational
        foreach ($Conflict in @($Result.virtualization.ConflictingEvidence)) {
            $ConflictLabel = switch ($Conflict) {
                "cim.firmware_false_but_operational" {
                    if ($Result.virtualization.nativeArchitecture -eq "ARM64") {
                        "Firmware virtualisation CIM field reported false but is not authoritative on ARM64"
                    } else {
                        "Firmware virtualisation CIM field reported false but operational evidence overrides it"
                    }
                }
                "cim.slat_false_not_authoritative_on_arm64" {
                    "SLAT CIM field reported false but is not authoritative on ARM64"
                }
                "systeminfo.firmware_false_but_operational" {
                    "System firmware metadata conflicts with active virtualisation evidence"
                }
                default { $Conflict }
            }
            Write-ITPPrerequisiteValue "WARNING" "Conflicting evidence" $ConflictLabel
        }
        Write-Output ""

        Write-ITPPrerequisiteSection "Windows Features"
        Write-ITPPrerequisiteValue $(if ($Result.windowsFeatures.WSL -eq "Enabled") { "PASS" } else { "FAIL" }) `
            "Windows Subsystem for Linux" $Result.windowsFeatures.WSL
        Write-ITPPrerequisiteValue $(if ($Result.windowsFeatures.VirtualMachinePlatform -eq "Enabled") { "PASS" } else { "FAIL" }) `
            "Virtual Machine Platform" $Result.windowsFeatures.VirtualMachinePlatform
        Write-ITPPrerequisiteValue "INFO" "WSL version" $Result.windowsFeatures.WSLVersion
        Write-ITPPrerequisiteValue "INFO" "WSL kernel" $Result.windowsFeatures.WSLKernelVersion
        Write-ITPPrerequisiteValue "INFO" "Default WSL version" $Result.windowsFeatures.DefaultWSLVersion
        Write-ITPPrerequisiteValue "INFO" "Hyper-V feature" $Result.windowsFeatures.HyperV
        Write-ITPPrerequisiteValue "INFO" "Windows Hypervisor Platform" `
            $Result.windowsFeatures.WindowsHypervisorPlatform
        Write-Output ""

        Write-ITPPrerequisiteSection "Applications"
        foreach ($Check in $Result.Checks | Where-Object {
                $_.Name -in @("Git", "Python", "pip", "WinGet",
                    "Microsoft Desktop App Installer") }) {
            Write-ITPPrerequisiteValue $Check.State $Check.Name $Check.Detail
        }
        Write-ITPPrerequisiteValue $(if ($Result.docker.DesktopInstalled) { "PASS" } else { "FAIL" }) `
            "Docker Desktop installed" $Result.docker.DesktopInstalled
        Write-ITPPrerequisiteValue $(if ($Result.docker.DesktopRunning) { "PASS" } else { "INFO" }) `
            "Docker Desktop running" $Result.docker.DesktopRunning
        Write-ITPPrerequisiteValue $(if ($Result.docker.CliAvailable) { "PASS" } else { "FAIL" }) `
            "Docker CLI" $Result.docker.CliAvailable
        Write-ITPPrerequisiteValue $(if ($Result.docker.DaemonReachable) { "PASS" } else { "FAIL" }) `
            "Docker daemon" $Result.docker.DaemonReachable
        Write-ITPPrerequisiteValue $(if ($Result.docker.ComposeV2) { "PASS" } else { "FAIL" }) `
            "Docker Compose v2" $Result.docker.ComposeV2
        Write-ITPPrerequisiteValue "INFO" "Docker backend" $Result.docker.Backend
        Write-Output ""

        Write-ITPPrerequisiteSection "System State"
        Write-ITPPrerequisiteValue $(if ($Result.rebootRequired) { "WARNING" } else { "PASS" }) `
            "Pending restart" $Result.rebootRequired
        Write-ITPPrerequisiteValue "INFO" "Interactive" $Result.interactive
        Write-Output ""

        Write-ITPPrerequisiteSection "Deployment"
        Write-Output "Automatically repairable:"
        if ($Result.repairableItems.Count -eq 0) { Write-Output "- None" }
        else {
            $Result.repairableItems | ForEach-Object {
                Write-Output "- $(Get-ITPPrerequisiteItemLabel $_) ($_)"
            }
        }
        Write-Output "Blocking:"
        if ($Result.blockingItems.Count -eq 0) { Write-Output "- None" }
        else {
            $Result.blockingItems | ForEach-Object {
                Write-Output "- $(Get-ITPPrerequisiteItemLabel $_) ($_)"
            }
        }
    }
    Write-Output ""
    Write-Output $(if (-not $Result.Applicable) {
        "Windows deployment readiness was not evaluated on this host."
    } elseif ($Result.Ready) {
        "Deployment prerequisites are ready."
    } else {
        "Deployment prerequisites are not ready."
    })
    $script:ITPPrerequisiteExitCode = $Result.ExitCode
}
