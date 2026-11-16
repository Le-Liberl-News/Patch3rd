# Installer source reference

The installer deltas use the supplied English v2022.09.22 game files.
`manifest.json` records SHA-256 hashes and sizes for every release source file.
Only the four files differing from the compilation toolchain are stored here;
the remaining files reuse the identical toolchain originals, with hash checks.

Compilation inputs are deliberately unchanged. `scripts/release.py` encodes the
same French outputs against this installer reference and verifies each delta by
decoding it back. No original game files are included in published patch assets.

The English videos were also checked against `videos/originaux.json` and match.
