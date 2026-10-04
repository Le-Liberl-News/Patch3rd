"""Rebuilds The 3rd's quest table (t_quest._dt) with the translations of the
"Portes (quêtes)" table. A text with no translation stays English.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import third_quest  # noqa: E402


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
    section = next((item for item in document.get("lexicon_sections", []) if item.get("stable_key") == "quetes"), None)
    translations: dict[str, bytes] = {}
    problems = []
    for entry in (section or {}).get("entries", []):
        french = str(entry.get("translation_fr") or "")
        if french.strip() and french != str(entry.get("source_en") or ""):
            try:
                translations[str(entry["external_key"])] = core.encode_game_text(french)
            except Exception as error:  # noqa: BLE001 - reported with the text
                problems.append(f"« {str(entry.get('source_en'))[:40]} » : {error}")
    if problems:
        print("ERREUR: Textes des quêtes refusés :\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    if not translations:
        print(json.dumps({"translated": 0}))
        return 0

    used = set()

    def replace(raw: bytes) -> bytes:
        key = hashlib.sha1(raw).hexdigest()[:16]
        if key in translations:
            used.add(key)
            return translations[key]
        return raw

    base = (arguments.base / "t_quest._dt").read_bytes()
    try:
        output = third_quest.rebuild(base, replace)
    except third_quest.QuestError as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        return 1
    arguments.output.mkdir(parents=True, exist_ok=True)
    (arguments.output / "t_quest._dt").write_bytes(output)
    print(json.dumps({"translated": len(used), "size": len(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
