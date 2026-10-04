# Patch français de Trails in the Sky the 3rd — Liberl News

Compilation et publication du patch de test de The 3rd, lancées depuis la
plateforme de traduction (bouton « Compiler »).

## Télécharger le patch

Les [releases](../../releases) contiennent les 5 dernières versions de test.
Chacune ne contient que des **différences** (xdelta) avec les fichiers anglais
d'origine du jeu : aucun fichier du jeu n'est publié.

1. Décompresser la release.
2. Lancer `appliquer.bat` (ou passer par l'installateur Liberl News).

Au premier passage, les fichiers anglais d'origine sont copiés dans `dat_en`,
dans le dossier du jeu ; chaque mise à jour repart de ces fichiers.
`restaurer.bat` remet le jeu en anglais.

## Contenu du dépôt

| Dossier | Contenu |
|---|---|
| `data/` | Historique des traductions (texte français uniquement) et images traduites, mis à jour à chaque compilation |
| `build/` | Compilateur (runner de la plateforme, toolchain : outils, catalogues) |
| `base/` | Fichiers originaux du jeu nécessaires à la compilation, **chiffrés** (AES-256) : la clé n'existe que dans les secrets du dépôt |
| `scripts/` | Historique (`history.py`), fabrication de la release en xdelta (`release.py`) |
| `patch/` | Applicateur Windows joint à chaque release (`appliquer.bat`, `restaurer.bat`, xdelta3) |

## Secrets du dépôt

- `PATCH3RD_KEY` : clé de déchiffrement de `base/`.
- `PLATFORM_TOKEN` : jeton d'API de la plateforme (lecture de l'export de compilation).

## Mettre à jour les fichiers originaux

Depuis les fichiers anglais d'origine (dossier `toolchain/` du site, DT21 de
l'installation anglaise), avec la clé dans `PATCH3RD_KEY` :

```sh
tar -cf - -C <toolchain> base_archives base_exe base_clm fonts_fr third-exe-strings.json \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:PATCH3RD_KEY \
  | split -b 90000000 -d -a 2 - base/originaux.tar.enc.
```
