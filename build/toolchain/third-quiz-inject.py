"""Rebuilds The 3rd's quiz tables (t_quiz01-04._dt) with the translations of
the "Quiz" table; a file without translations is left as it is.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import third_quiz  # noqa: E402


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
    section = next((item for item in document.get("lexicon_sections", []) if item.get("stable_key") == "quiz"), None)
    translations: dict[str, dict[tuple[int, int], bytes]] = {}
    problems = []
    for entry in (section or {}).get("entries", []):
        french = str(entry.get("translation_fr") or "")
        english = str(entry.get("source_en") or "")
        if not french.strip() or french == english:
            continue
        name, question, field = str(entry["external_key"]).split(":")
        if third_quiz.CONTROL.findall(french) != third_quiz.CONTROL.findall(english):
            problems.append(f"« {english[:40]} » : les codes {{x..}} doivent rester les mêmes, dans le même ordre")
            continue
        try:
            translations.setdefault(name, {})[(int(question), int(field))] = third_quiz.to_bytes(core, french)
        except Exception as error:  # noqa: BLE001 - reported with the text
            problems.append(f"« {english[:40]} » : {error}")
    if problems:
        print("ERREUR: Textes du quiz refusés :\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    arguments.output.mkdir(parents=True, exist_ok=True)
    for name, changes in sorted(translations.items()):
        base = (arguments.base / f"{name}._dt").read_bytes()
        try:
            rebuilt = third_quiz.rebuild(base, lambda question, field, raw: changes.get((question, field), raw))
        except third_quiz.QuizError as error:
            print(f"ERREUR: {name} : {error}", file=sys.stderr)
            return 1
        (arguments.output / f"{name}._dt").write_bytes(rebuilt)
    print(json.dumps({"files": len(translations), "texts": sum(len(item) for item in translations.values())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
