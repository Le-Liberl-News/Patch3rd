"""Rebuilds the battle scripts of The 3rd that have translated lines in the
"Combats (AS)" table.

Reads the extracted English ED6_DT30 (--base), writes the changed as*._dt
files to --output. Each file is rebuilt by third_as.rebuild, which checks
that only the texts changed and that every code address was moved alike.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import third_as  # noqa: E402


def load_core(path: Path):
    spec = importlib.util.spec_from_file_location("lexique_dt", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["lexique_dt"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    arguments = parser.parse_args()

    core = load_core(arguments.core)
    with gzip.open(arguments.export, "rt", encoding="utf-8") as stream:
        document = json.load(stream)
    section = next((item for item in document.get("lexicon_sections", []) if item.get("stable_key") == "as"), None)

    by_file: dict[str, dict[str, dict]] = {}
    problems = []
    for entry in (section or {}).get("entries", []):
        key = str(entry.get("external_key") or "")
        french = str(entry.get("translation_fr") or "")
        english = str(entry.get("source_en") or "")
        name, _, place = key.partition(":")
        if not french.strip() or (french == english and not place.startswith("voice@")):
            continue
        try:
            encoded = core.encode_game_text(french)
            targets = by_file.setdefault(name, {"replacements": {}, "insertions": {}})
            if place.startswith("voice@"):
                targets["insertions"][int(place[6:], 16)] = encoded
            else:
                targets["replacements"][place] = encoded
        except Exception as error:  # noqa: BLE001 - reported with the line
            problems.append(f"{key} « {english[:40]} » : {error}")

    arguments.output.mkdir(parents=True, exist_ok=True)
    for name, targets in sorted(by_file.items()):
        source = arguments.base / f"{name}._dt"
        if not source.is_file():
            problems.append(f"{name} : fichier absent de l'archive de base")
            continue
        try:
            (arguments.output / source.name).write_bytes(third_as.rebuild(
                source.read_bytes(), targets["replacements"], targets["insertions"]
            ))
        except third_as.AsError as error:
            problems.append(f"{name} : {error}")
    if problems:
        print("ERREUR: Répliques de combat refusées :\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    print(json.dumps({
        "files": len(by_file),
        "lines": sum(len(item["replacements"]) + len(item["insertions"]) for item in by_file.values()),
        "inserted_saytexts": sum(len(item["insertions"]) for item in by_file.values()),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
