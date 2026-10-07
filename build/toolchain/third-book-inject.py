"""Rebuild The 3rd's books from the platform's complete page lists.

Book indices refer to the base file's books; page 0 is the title. Body pages
are rebuilt in contiguous page_index order, so inserted/deleted pages are
reflected in the game. The platform's French is used exactly, empty included.
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


def rebuild_books(core, name: str, base: bytes, rows: list[dict]) -> bytes:
    if name not in third_book.FILES:
        raise ValueError(f"{name} : fichier de livres inconnu")
    original = third_book.read(core, base)
    pages: dict[int, dict[int, str]] = {}
    for row in rows:
        book = int(row["book_index"])
        page = int(row["page_index"])
        if not 0 <= book < len(original) or page < 0:
            raise ValueError(f"{name} : indice de livre/page invalide ({book}, {page})")
        target = pages.setdefault(book, {})
        if page in target:
            raise ValueError(f"{name} livre {book + 1} : page {page} dupliquée")
        target[page] = str(row.get("translation_fr") or "")
    rebuilt = []
    for book in range(len(original)):
        texts = pages.get(book)
        if texts is None:
            raise ValueError(f"{name} livre {book + 1} : absent de la plateforme")
        if sorted(texts) != list(range(len(texts))) or len(texts) < 2:
            raise ValueError(f"{name} livre {book + 1} : titre/pages manquants ou non contigus")
        rebuilt.append({"title": texts[0], "pages": [texts[page] for page in range(1, len(texts))]})
    output = third_book.write(core, rebuilt)
    expected = [{"title": core.decode_game_text(core.encode_game_text(book["title"])),
                 "pages": [third_book.decode_page(core, third_book.encode_page(core, page))
                           for page in book["pages"]]} for book in rebuilt]
    if third_book.read(core, output) != expected:
        raise ValueError(f"{name} : livres différents après relecture")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    arguments = parser.parse_args()
    core = load_core(arguments.core)
    with gzip.open(arguments.export, "rt", encoding="utf-8") as stream:
        document = json.load(stream)
    by_file: dict[str, list[dict]] = {}
    for row in document.get("books", []):
        by_file.setdefault(str(row["file_code"]), []).append(row)
    problems = []
    staged = {}
    for name, rows in sorted(by_file.items()):
        try:
            # Validate the name before resolving it against the base folder.
            if name not in third_book.FILES:
                raise ValueError(f"{name} : fichier de livres inconnu")
            staged[name] = rebuild_books(core, name, (arguments.base / f"{name}._dt").read_bytes(), rows)
        except Exception as error:  # report all files, publish no partial result
            problems.append(f"{name} : {error}")
    if problems:
        print("ERREUR: Livres refusés :\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    arguments.output.mkdir(parents=True, exist_ok=True)
    for name, data in staged.items():
        (arguments.output / f"{name}._dt").write_bytes(data)
    print(json.dumps({"files": len(staged), "pages": sum(len(rows) for rows in by_file.values())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
