# Vidéos françaises

Les vidéos du jeu sont des `.dat` sans `.dir` à la racine du jeu :
`ED6_DT48.dat`, `ED6_DT49.dat`, `ED6_DT50.dat` (MPEG) et `ED6_DT51.dat`
(AVI de 5 min 22, l'ending).

Pour ajouter une vidéo traduite : la mettre ici sous le **même nom** que le
fichier du jeu (par exemple `videos/ED6_DT51.dat`) et commiter. Chaque build
suivant l'ajoute à la release (un delta, appliqué par l'installateur et
`appliquer.bat` comme les autres fichiers ; désinstaller remet l'anglais).

GitHub refuse les fichiers de plus de 100 Mo : une vidéo plus grosse se
découpe en morceaux `ED6_DT51.dat.part00`, `.part01`… (recollés dans l'ordre
par le build). Dans PowerShell, depuis ce dossier :

```powershell
$f = 'ED6_DT51.dat'; $s = [IO.File]::OpenRead($f); $b = New-Object byte[] 90MB; $i = 0
while (($n = $s.Read($b, 0, $b.Length)) -gt 0) { [IO.File]::WriteAllBytes(('{0}.part{1:D2}' -f $f, $i++), $b[0..($n-1)]) }
$s.Close(); Remove-Item $f
```

`originaux.json` donne l'empreinte des vidéos anglaises d'origine : seul le
joueur qui a ces fichiers-là reçoit la vidéo française.
