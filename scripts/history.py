"""Writes the platform's French texts and translated pictures into data/, so
that each build leaves a readable history in the repository (git diff shows
what changed between two releases).

    python scripts/history.py job/export.json data

Only the French side goes in: the Japanese and English texts belong to the
game and stay out of this public repository.
"""
from __future__ import annotations

import base64
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path


def write(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    export = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    target = Path(sys.argv[2])
    shutil.rmtree(target, ignore_errors=True)

    # Scripts: one file per scene file, the French of each bubble by its number.
    scripts: dict[str, list] = defaultdict(list)
    for entry in export.get("entries", []):
        french = str(entry.get("translation_fr") or "")
        if french.strip():
            scripts[str(entry["script"]).lower()].append({"n": int(entry["ordinal"]), "fr": french})
    for code, rows in scripts.items():
        write(target / "scripts" / f"{code}.json", sorted(rows, key=lambda row: row["n"]))

    # Speaker names of each file.
    names: dict[str, list] = defaultdict(list)
    for character in export.get("characters", []):
        french = str(character.get("translation_fr") or "")
        if french.strip():
            names[str(character["script"]).lower()].append({"n": int(character["ordinal"]), "fr": french})
    for code, rows in names.items():
        write(target / "noms" / f"{code}.json", sorted(rows, key=lambda row: row["n"]))

    # Lexicon, tables, executable, battle lines, doors, quiz.
    for section in export.get("lexicon_sections", []):
        rows = [
            {"cle": entry.get("external_key") or entry.get("ordinal"), "type": entry.get("field_kind"), "fr": entry["translation_fr"]}
            for entry in section.get("entries", []) if str(entry.get("translation_fr") or "").strip()
        ]
        if rows:
            write(target / "tables" / f"{section['stable_key']}.json", rows)

    # Books, page by page.
    books = [
        {"fichier": row["file_code"], "livre": int(row["book_index"]), "page": int(row["page_index"]), "fr": row["translation_fr"]}
        for row in export.get("books", []) if str(row.get("translation_fr") or "").strip()
    ]
    if books:
        write(target / "livres.json", books)

    # Translated pictures, as uploaded (PNG).
    for key, encoded in sorted((export.get("game_images") or {}).items()):
        path = target / "images" / f"{key}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))

    print(f"Historique : {len(scripts)} scripts, {sum(len(rows) for rows in scripts.values())} bulles, "
          f"{len(export.get('game_images') or {})} images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
