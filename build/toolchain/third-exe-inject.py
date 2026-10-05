"""Puts the translated texts of the "Exécutable" table into The 3rd's two
executables (DX8 ed6_win3.exe, DX9 ed6_win3_DX9.exe), after the code changes
of Sky SC's French executable (third_exe_code.py: French character widths,
accents 0xA0-0xDF).

The French texts go into a new section of each executable; every code pointer
listed for a text in third-exe-strings.json is redirected there. The English
texts stay where they are (nothing else reads them). After the injection each
pointer is read back and must lead to the exact encoded translation.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from third_exe_code import ExeCodeError, apply_code_patches, load_widths  # noqa: E402
from third_exe_core import PeImage  # noqa: E402

# printf-style fields: the game passes its values in the English order. A
# translation keeps the same kinds of fields in that order; it may drop the
# last ones (their values are then not read) and change a width (%8s -> %s),
# as Sky SC's French did, but never add, skip or swap one (crash or garbage).
FORMAT_FIELD = re.compile(r"%[-+ #0]*[0-9]*(?:\.[0-9]+)?([hlL]?[cdiouxXeEfgGsp%])")


def field_kinds(text: str) -> list[str]:
    return [kind for kind in FORMAT_FIELD.findall(text) if kind != "%"]


def fields_compatible(french: str, english: str) -> bool:
    kinds = field_kinds(french)
    return kinds == field_kinds(english)[:len(kinds)]
# Sheets and the platform lose tab characters: "\t" stands for one.
TAB_ESCAPE = "\\t"


class ExeInjectError(RuntimeError):
    pass


def load_core(path: Path):
    spec = importlib.util.spec_from_file_location("lexique_dt", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["lexique_dt"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--strings", required=True, type=Path)
    parser.add_argument("--dx8", required=True, type=Path)
    parser.add_argument("--dx9", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    arguments = parser.parse_args()

    core = load_core(arguments.core)
    sources = json.loads(arguments.strings.read_text(encoding="utf-8"))
    dx8_data, dx9_data = arguments.dx8.read_bytes(), arguments.dx9.read_bytes()
    for label, data, expected in (("DX8", dx8_data, sources["dx8_sha256"]), ("DX9", dx9_data, sources["dx9_sha256"])):
        if hashlib.sha256(data).hexdigest() != expected:
            raise ExeInjectError(f"Exécutable {label} de base différent de celui des pointeurs relevés")
    by_key = {row["key"]: row for row in sources["strings"]}

    with gzip.open(arguments.export, "rt", encoding="utf-8") as stream:
        document = json.load(stream)
    section = next((item for item in document.get("lexicon_sections", []) if item.get("stable_key") == "exe"), None)
    entries = section["entries"] if section else []

    rows = []
    problems = []
    for entry in entries:
        key = str(entry.get("external_key") or "")
        french = str(entry.get("translation_fr") or "")
        source = by_key.get(key)
        if source is None:
            problems.append(f"{key} : texte inconnu des pointeurs relevés")
            continue
        # The platform's text as is (empty = empty).
        if french == source["en"]:
            continue
        if not fields_compatible(french, source["en"]):
            problems.append(
                f"{key} « {source['en'][:40]} » : les champs {' '.join('%' + kind for kind in field_kinds(source['en'])) or '(aucun)'} "
                f"doivent rester dans cet ordre (on peut omettre les derniers, pas en ajouter ni en intervertir)"
            )
            continue
        try:
            encoded = core.encode_game_text(french.replace(TAB_ESCAPE, "\t"), b"\n")
        except Exception as error:  # noqa: BLE001 - reported with the row
            problems.append(f"{key} « {source['en'][:40]} » : {error}")
            continue
        rows.append((source, encoded))
    if problems:
        raise ExeInjectError("Textes de l'exécutable refusés :\n- " + "\n- ".join(problems))

    widths = load_widths()
    outputs = {}
    code_reports = {}
    for label, data, field in (("DX8", dx8_data, "dx8"), ("DX9", dx9_data, "dx9")):
        image = PeImage(data, label)
        code_reports[label] = apply_code_patches(image, widths)
        if rows:
            addresses = image.add_string_section([encoded for _, encoded in rows])
            for source, encoded in rows:
                for offset in source[field]:
                    struct.pack_into("<I", image.data, offset, addresses[encoded])
            for source, encoded in rows:
                for offset in source[field]:
                    target = image.pointer_target(offset)
                    if target is None or target[1] != encoded:
                        raise ExeInjectError(f"{label} : contrôle après injection en échec à 0x{offset:X} ({source['key']})")
        outputs[label] = bytes(image.data)

    arguments.output.mkdir(parents=True, exist_ok=True)
    (arguments.output / "ed6_win3.exe").write_bytes(outputs["DX8"])
    (arguments.output / "ed6_win3_DX9.exe").write_bytes(outputs["DX9"])
    print(json.dumps({
        "translated": len(rows),
        "dx8_pointers": sum(len(source["dx8"]) for source, _ in rows),
        "dx9_pointers": sum(len(source["dx9"]) for source, _ in rows),
        "code": code_reports["DX8"],
    }))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ExeInjectError, ExeCodeError) as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        raise SystemExit(1)
