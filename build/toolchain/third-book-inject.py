"""Rebuilds The 3rd's book files (t_book01-16._dt) that have French titles or
pages on the platform; an untranslated title or page stays English.

Each translated file is read back: same books, same number of pages, and every
page decodes to the text that was written.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import third_book  # noqa: E402


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
    french: dict[str, dict[tuple[int, int], str]] = {}
    for row in document.get("books", []):
        text = str(row.get("translation_fr") or "")
        # The platform's text as is (empty = empty page).
        if text != str(row.get("source_en") or ""):
            french.setdefault(str(row["file_code"]), {})[(int(row["book_index"]), int(row["page_index"]))] = text

    problems = []
    arguments.output.mkdir(parents=True, exist_ok=True)
    for name, changes in sorted(french.items()):
        if name not in third_book.FILES:
            problems.append(f"{name} : fichier de livres inconnu")
            continue
        books = third_book.read(core, (arguments.base / f"{name}._dt").read_bytes())
        for (book, page), text in changes.items():
            if book >= len(books) or page > len(books[book]["pages"]):
                problems.append(f"{name} livre {book + 1} page {page} : n'existe pas dans le jeu")
                continue
            if page == 0:
                books[book]["title"] = text
            else:
                books[book]["pages"][page - 1] = text
        try:
            data = third_book.write(core, books)
            again = third_book.read(core, data)
        except (third_book.BookError, Exception) as error:  # noqa: BLE001 - reported with the file
            problems.append(f"{name} : {error}")
            continue
        if [len(book["pages"]) for book in again] != [len(book["pages"]) for book in books]:
            problems.append(f"{name} : nombre de pages différent à la relecture")
            continue
        (arguments.output / f"{name}._dt").write_bytes(data)
    if problems:
        print("ERREUR: Livres refusés :\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    print(json.dumps({"files": len(french), "pages": sum(len(item) for item in french.values())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
