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
        default {
            throw "Unsupported or ambiguous native Windows architecture '$Raw'. ITP supports AMD64 and ARM64."
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
        }
    }

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
    $Blocking = @($Checks | Where-Object { $_.State -eq "FAIL" -and $_.Classification -eq "blocking" })
    return [PSCustomObject]@{
        Checks = $Checks
        ExitCode = $(if ($Blocking.Count -eq 0) { 0 } else { 1 })
        Ready = $Blocking.Count -eq 0
        Applicable = $true
    }
}

function Invoke-ITPPrerequisiteDiagnostics {
    param([Nullable[bool]]$InteractiveOverride = $null)
    $Result = Get-ITPPrerequisiteDiagnostics -InteractiveOverride $InteractiveOverride
    Write-Output "ITP Windows prerequisite diagnostics"
    foreach ($Check in $Result.Checks) {
        Write-Output ("[{0}] {1} - {2} ({3})" -f `
            $Check.State, $Check.Name, $Check.Detail, $Check.Classification)
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
