# Applique le patch français de Trails in the Sky the 3rd (Liberl News), sans
# l'installateur. Même principe que l'installateur LN : les fichiers anglais
# d'origine sont copiés une fois dans « dat_en », et chaque version du patch
# est appliquée à ces fichiers-là (on peut donc installer n'importe quelle
# version par-dessus une autre). restaurer.bat remet l'anglais.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$xdelta = Join-Path $here 'xdelta3.exe'
$manifest = Get-Content -Raw -Encoding UTF8 (Join-Path $here 'patch.json') | ConvertFrom-Json

function Find-Game {
    $candidates = @(
        $here,
        (Split-Path -Parent $here),
        'C:\Program Files (x86)\Steam\steamapps\common\Trails in the Sky the 3rd',
        'C:\Program Files\Steam\steamapps\common\Trails in the Sky the 3rd',
        'C:\GOG Games\Trails in the Sky the 3rd'
    )
    foreach ($folder in $candidates) {
        if ($folder -and (Test-Path (Join-Path $folder 'ED6_DT21.dir'))) { return $folder }
    }
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choisissez le dossier de Trails in the Sky the 3rd (celui qui contient ed6_win3.exe)'
    if ($dialog.ShowDialog() -ne 'OK') { return $null }
    if (Test-Path (Join-Path $dialog.SelectedPath 'ED6_DT21.dir')) { return $dialog.SelectedPath }
    return $null
}

function Get-Sha([string] $path) {
    if (-not (Test-Path -LiteralPath $path)) { return '' }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
}

# Fenêtre source couvrant tout l'original (même règle que scripts/release.py).
function Get-Window([string] $path) {
    $size = (Get-Item -LiteralPath $path).Length
    return [int64][math]::Max(64MB, [math]::Floor(($size + 1MB) / 1MB) * 1MB)
}

Write-Host "Patch français de Trails in the Sky the 3rd - version $($manifest.version)" -ForegroundColor Yellow
$game = Find-Game
if (-not $game) {
    Write-Host "Dossier du jeu introuvable : le patch n'a pas été appliqué." -ForegroundColor Red
    exit 1
}
Write-Host "Jeu : $game"
$english = Join-Path $game 'dat_en'

# Tous les fichiers sont vérifiés avant d'en modifier un seul : un jeu qui
# n'est pas la version sur laquelle le patch est construit reste intact.
$mismatched = @()
foreach ($file in $manifest.files) {
    if ((Get-Sha (Join-Path $english $file.file)) -eq $file.original_sha256) { continue }
    $current = Get-Sha (Join-Path $game $file.file)
    if ($current -ne $file.original_sha256 -and $current -ne $file.patched_sha256) { $mismatched += $file.file }
}
if ($mismatched.Count -gt 0) {
    # Version anglaise du patch : date de compilation de son ed6_win3.exe.
    $baseStamp = if ($manifest.base.timestamp) { [int64]$manifest.base.timestamp } else { 1645723089 }
    $baseDate = [DateTimeOffset]::FromUnixTimeSeconds($baseStamp).UtcDateTime
    $exeDate = $null
    try {
        $bytes = [IO.File]::ReadAllBytes((Join-Path $game 'ed6_win3.exe'))
        $exeDate = [DateTimeOffset]::FromUnixTimeSeconds([BitConverter]::ToUInt32($bytes, [BitConverter]::ToInt32($bytes, 0x3C) + 8)).UtcDateTime
    } catch {}
    $culture = [Globalization.CultureInfo]::GetCultureInfo('fr-FR')
    Write-Host ''
    Write-Host "Le patch n'a pas été installé : rien n'a été modifié dans votre jeu." -ForegroundColor Red
    if ($exeDate -and [Math]::Abs(($exeDate - $baseDate).TotalHours) -gt 24 -and $exeDate -gt $baseDate) {
        Write-Host ("Votre jeu est plus récent que la version anglaise sur laquelle ce patch de test est construit : votre ed6_win3.exe date du {0}, celui du patch du {1}. Ce n'est pas un problème de votre côté : le patch sera mis à jour pour la dernière version du jeu." -f $exeDate.ToString('d MMMM yyyy', $culture), $baseDate.ToString('d MMMM yyyy', $culture)) -ForegroundColor Yellow
    } elseif ($exeDate -and [Math]::Abs(($exeDate - $baseDate).TotalHours) -gt 24) {
        Write-Host ("Votre jeu est plus ancien que la version du patch (votre ed6_win3.exe date du {0}, celui du patch du {1}) : mettez-le à jour sur Steam ou GOG, puis relancez appliquer.bat." -f $exeDate.ToString('d MMMM yyyy', $culture), $baseDate.ToString('d MMMM yyyy', $culture)) -ForegroundColor Yellow
    } else {
        Write-Host "Votre jeu est de la bonne version, mais certains fichiers ont été modifiés (autre patch, mod, fichiers abîmés). Vérifiez l'intégrité des fichiers du jeu (Steam : Propriétés > Fichiers installés > Vérifier ; GOG Galaxy : Gérer l'installation > Vérifier / Réparer), puis relancez appliquer.bat." -ForegroundColor Yellow
    }
    Write-Host "Fichiers concernés : $($mismatched -join ', ')"
    exit 1
}
New-Item -ItemType Directory -Force -Path $english | Out-Null

$failures = @()
foreach ($file in $manifest.files) {
    $name = $file.file
    $destination = Join-Path $game $name
    $saved = Join-Path $english $name
    Write-Host "$name..." -NoNewline
    try {
        # L'anglais d'origine : déjà dans dat_en, sinon le fichier du jeu s'il
        # est encore anglais (il est alors copié dans dat_en).
        if ((Get-Sha $saved) -ne $file.original_sha256) {
            if ((Get-Sha $destination) -ne $file.original_sha256) {
                throw "fichier anglais d'origine introuvable (ni dans dat_en, ni dans le jeu)."
            }
            Copy-Item -LiteralPath $destination -Destination $saved -Force
        }
        if ((Get-Sha $destination) -eq $file.patched_sha256) {
            Write-Host ' déjà à jour.' -ForegroundColor Green
            continue
        }
        $temporary = "$destination.fr.tmp"
        & $xdelta -d -f -B (Get-Window $saved) -s $saved (Join-Path $here $file.delta) $temporary
        if ($LASTEXITCODE -ne 0) { throw "xdelta3 a échoué ($LASTEXITCODE)" }
        if ((Get-Sha $temporary) -ne $file.patched_sha256) {
            Remove-Item -LiteralPath $temporary -Force
            throw 'le fichier français obtenu ne correspond pas.'
        }
        Move-Item -LiteralPath $temporary -Destination $destination -Force
        Write-Host ' installé.' -ForegroundColor Green
    } catch {
        Write-Host " ÉCHEC : $($_.Exception.Message)" -ForegroundColor Red
        $failures += $name
    }
}

Write-Host ''
if ($failures.Count -eq 0) {
    Write-Host "Patch installé. Les fichiers anglais d'origine sont dans « dat_en » (restaurer.bat les remet en place)." -ForegroundColor Green
    exit 0
}
Write-Host "Fichiers non patchés : $($failures -join ', ')" -ForegroundColor Red
Write-Host "Vérifiez l'intégrité des fichiers du jeu (Steam : Propriétés > Fichiers installés > Vérifier ; GOG Galaxy : Gérer l'installation > Vérifier / Réparer), puis relancez appliquer.bat." -ForegroundColor Yellow
exit 1
