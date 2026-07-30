$script:ITPMinimumPython = [Version]"3.9"
$script:ITPPythonPackage = "Python.Python.3.12"
$script:ITPPythonDownload = "https://www.python.org/downloads/windows/"

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
        # Missing launchers and broken Windows App Execution Aliases are normal
        # probe failures.
        return $null
    }
}

function Get-ITPPythonCandidates {
    param([switch]$IncludeInstallLocations)

    $Candidates = @(
        [PSCustomObject]@{ Name = "py"; Prefix = @("-3"); Label = "py -3"; Path = $null },
        [PSCustomObject]@{ Name = "python"; Prefix = @(); Label = "python"; Path = $null },
        [PSCustomObject]@{ Name = "python3"; Prefix = @(); Label = "python3"; Path = $null }
    )
    if ($IncludeInstallLocations -and $env:LOCALAPPDATA) {
        $Candidates += @(
            [PSCustomObject]@{
                Name = $null; Prefix = @(); Label = "Python 3.12 per-user install"
                Path = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
            },
            [PSCustomObject]@{
                Name = $null; Prefix = @(); Label = "WinGet Python 3.12 install"
                Path = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\python.exe"
            }
        )
    }
    return $Candidates
}

function Find-ITPPython {
    param(
        [switch]$IncludeInstallLocations,
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
    foreach ($Candidate in (Get-ITPPythonCandidates -IncludeInstallLocations:$IncludeInstallLocations)) {
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
        [scriptblock]$ConsentReader = {
            Read-Host "Install Python 3.12 using WinGet? [Y/n]"
        },
        [scriptblock]$WingetRunner = {
            param($Executable, $Arguments)
            & $Executable @Arguments | Out-Host
            return $LASTEXITCODE
        },
        [scriptblock]$PathReader = {
            param($Scope)
            [Environment]::GetEnvironmentVariable("Path", $Scope)
        },
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

    $WingetCommand = & $CommandResolver "winget"
    if ($null -eq $WingetCommand) {
        throw (
            "WinGet is unavailable, so ITP cannot install Python automatically.`n" +
            "Install Python from $script:ITPPythonDownload and enable 'Add python.exe to PATH', " +
            "then open a new PowerShell window and rerun the deployment.")
    }

    if (-not (Test-ITPInteractiveDeployment -Arguments $Arguments -InteractiveOverride $InteractiveOverride)) {
        throw (
            "ITP will not install software without consent. This invocation is non-interactive; " +
            "install $script:ITPPythonPackage with WinGet or install Python from " +
            "$script:ITPPythonDownload, then rerun the deployment.")
    }

    [Console]::Error.WriteLine(
        "ITP can install the supported Python 3.12 release using WinGet package $script:ITPPythonPackage.")
    $Consent = (& $ConsentReader).Trim()
    if ($Consent -and $Consent -notmatch '^(?i:y|yes)$') {
        throw (
            "Python installation was declined. No software was installed. Install Python from " +
            "$script:ITPPythonDownload, enable 'Add python.exe to PATH', and rerun the deployment.")
    }

    $InstallArguments = @(
        "install", "--id", $script:ITPPythonPackage, "--exact", "--source", "winget",
        "--accept-package-agreements", "--accept-source-agreements"
    )
    $InstallExitCode = & $WingetRunner $WingetCommand.Source $InstallArguments
    if ($InstallExitCode -ne 0) {
        throw (
            "WinGet could not install $script:ITPPythonPackage (exit code $InstallExitCode). " +
            "Review the WinGet output, then retry or install Python from $script:ITPPythonDownload.")
    }

    Update-ITPProcessPath -PathReader $PathReader
    $Discovery = Find-ITPPython -IncludeInstallLocations `
        -CommandResolver $CommandResolver -Probe $Probe
    if ($Discovery.Selected) {
        return $Discovery.Selected
    }
    throw (
        "WinGet reported that Python installation completed, but ITP could not resolve a supported " +
        "interpreter in this process. Open a new PowerShell window and rerun the deployment.")
}
