"""Regression tests using the real book encoder/decoder and synthetic binaries."""
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOLCHAIN = ROOT / "build" / "toolchain"
sys.path.insert(0, str(TOOLCHAIN))
import third_book
import lexique_dt as core
spec = importlib.util.spec_from_file_location("book_inject", TOOLCHAIN / "third-book-inject.py")
inject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inject)


def rows(books):
    return [{"file_code": "t_book01", "book_index": book, "page_index": page,
             "source_en": "EN", "translation_fr": text}
            for book, item in enumerate(books)
            for page, text in enumerate([item["title"]] + item["pages"])]


class BookInjectionTests(unittest.TestCase):
    def setUp(self):
        self.books = [{"title": "First", "pages": ["One", "Two"]},
                      {"title": "Second", "pages": ["Three"]}]
        self.base = third_book.write(core, self.books)

    def rebuild(self, books):
        output = inject.rebuild_books(core, "t_book01", self.base, rows(books))
        return third_book.read(core, output)

    def test_insert_middle_and_tail(self):
        wanted = [{"title": "Premier", "pages": ["One", "Inserted", "Two", "Last"]}, self.books[1]]
        self.assertEqual(self.rebuild(wanted), wanted)

    def test_delete_page_and_relocate_following_book(self):
        wanted = [{"title": "Premier", "pages": ["Two"]}, self.books[1]]
        self.assertEqual(self.rebuild(wanted), wanted)

    def test_blank_pages_and_controls(self):
        wanted = [{"title": "Premier", "pages": ["", "Texte\n{C0}Suite{I9}{I3}{I768}#72x#210y#920F", ""]}, self.books[1]]
        self.assertEqual(self.rebuild(wanted), wanted)

    def test_missing_duplicate_negative_and_out_of_range(self):
        valid = rows(self.books)
        invalid = [valid[1:], valid + [valid[0]],
                   valid + [{**valid[0], "book_index": -1}],
                   valid + [{**valid[0], "book_index": 2}],
                   valid + [{**valid[0], "page_index": -1}],
                   [row for row in valid if row["book_index"] == 0],
                   [row for row in valid if not(row["book_index"] == 0 and row["page_index"] == 1)],
                   [row for row in valid if row["book_index"] != 0 or row["page_index"] == 0]]
        for case in invalid:
            with self.subTest(case=case), self.assertRaises(ValueError):
                inject.rebuild_books(core, "t_book01", self.base, case)

    def test_file_name_and_binary_limits(self):
        with self.assertRaises(ValueError):
            inject.rebuild_books(core, "../bad", self.base, rows(self.books))
        with self.assertRaises(third_book.BookError):
            self.rebuild([{"title": "Book", "pages": ["X" * 70000]}, self.books[1]])
        with self.assertRaises(third_book.BookError):
            self.rebuild([{"title": "Book", "pages": ["{x03}"]}, self.books[1]])

    def test_cli_does_not_publish_partial_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "base").mkdir()
            (root / "base/t_book01._dt").write_bytes(self.base)
            (root / "base/t_book02._dt").write_bytes(self.base)
            export = root / "export.json.gz"
            bad = rows(self.books) + [{**rows(self.books)[0], "file_code": "t_book02"}]
            with gzip.open(export, "wt", encoding="utf-8") as output:
                json.dump({"books": bad}, output)
            done = subprocess.run([sys.executable, str(TOOLCHAIN / "third-book-inject.py"),
                                   "--export", str(export), "--base", str(root / "base"),
                                   "--output", str(root / "out"), "--core", str(TOOLCHAIN / "lexique_dt.py")],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 1, done.stderr)
            self.assertFalse((root / "out/t_book01._dt").exists())
            with gzip.open(export, "wt", encoding="utf-8") as output:
                json.dump({"books": rows(self.books)}, output)
            done = subprocess.run([sys.executable, str(TOOLCHAIN / "third-book-inject.py"),
                                   "--export", str(export), "--base", str(root / "base"),
                                   "--output", str(root / "out"), "--core", str(TOOLCHAIN / "lexique_dt.py")],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(third_book.read(core, (root / "out/t_book01._dt").read_bytes()), self.books)


if __name__ == "__main__":
    unittest.main()
