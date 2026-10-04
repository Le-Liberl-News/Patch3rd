# Remet les fichiers anglais d'origine (dat_en, gardés par appliquer.bat ou l'installateur) dans le jeu.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$candidates = @($here, (Split-Path -Parent $here),
    'C:\Program Files (x86)\Steam\steamapps\common\Trails in the Sky the 3rd',
    'C:\Program Files\Steam\steamapps\common\Trails in the Sky the 3rd',
    'C:\GOG Games\Trails in the Sky the 3rd')
$game = $candidates | Where-Object { $_ -and (Test-Path (Join-Path $_ 'dat_en')) } | Select-Object -First 1
if (-not $game) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choisissez le dossier de Trails in the Sky the 3rd'
    if ($dialog.ShowDialog() -eq 'OK') { $game = $dialog.SelectedPath }
}
$backup = if ($game) { Join-Path $game 'dat_en' } else { '' }
if (-not $game -or -not (Test-Path $backup)) {
    Write-Host 'Aucune sauvegarde anglaise trouvée.' -ForegroundColor Red
    exit 1
}
Get-ChildItem -LiteralPath $backup -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $game $_.Name) -Force
    Write-Host "$($_.Name) : anglais remis."
}
Write-Host "Jeu remis en anglais. La sauvegarde est conservée pour un prochain patch." -ForegroundColor Green
