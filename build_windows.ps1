param(
    [string]$PythonCommand = "py"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version = & $PythonCommand -3 (Join-Path $ProjectDir "dota2_review.py") --version
$ReleaseName = "Dota2ReviewCoach-v$Version"
$DistDir = Join-Path $ProjectDir "dist"
$BuildDir = Join-Path $ProjectDir "build"

& $PythonCommand -3 -m pip install --upgrade pyinstaller
& $PythonCommand -3 -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $ReleaseName `
    --distpath $DistDir `
    --workpath $BuildDir `
    --specpath $BuildDir `
    --add-data "$(Join-Path $ProjectDir 'dota_zh_names.json');." `
    --add-data "$(Join-Path $ProjectDir 'hero_names_zh.json');." `
    --add-data "$(Join-Path $ProjectDir 'assets\dark-arena-background.png');assets" `
    --add-data "$(Join-Path $ProjectDir 'assets\drow-mascot-cutout.png');assets" `
    --add-data "$(Join-Path $ProjectDir 'assets\windranger-mascot-cutout.png');assets" `
    (Join-Path $ProjectDir "dota2_review_gui.py")

$ExePath = Join-Path $DistDir "$ReleaseName.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "构建结束但未找到 $ExePath"
}

Write-Output "Windows EXE 已生成：$ExePath"
Get-FileHash -Algorithm SHA256 -LiteralPath $ExePath
