$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$Bootstrap = Join-Path $Root "scripts\bootstrap.py"

if (-not (Test-Path -LiteralPath $Bootstrap -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "ITP bootstrap error: missing scripts\bootstrap.py. Restore the repository and rerun .\itp.ps1.")
    exit 1
}

$Candidates = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $Candidates += ,@("py", "-3")
}
if (Get-Command python3 -ErrorAction SilentlyContinue) {
    $Candidates += ,@("python3")
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    $Candidates += ,@("python")
}

$Python = $null
foreach ($Candidate in $Candidates) {
    $Command = $Candidate[0]
    $Prefix = @($Candidate | Select-Object -Skip 1)
    & $Command @Prefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $Python = $Candidate
        break
    }
}

if ($null -eq $Python) {
    [Console]::Error.WriteLine("ITP bootstrap error: Python 3.9 or later was not found.")
    [Console]::Error.WriteLine(
        "Install Python from https://www.python.org/downloads/, then rerun .\itp.ps1.")
    exit 1
}

$PythonCommand = $Python[0]
$PythonPrefix = @($Python | Select-Object -Skip 1)
& $PythonCommand @PythonPrefix $Bootstrap @args
exit $LASTEXITCODE
