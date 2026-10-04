"""The 3rd's books (ED6_DT22/t_book00-16._dt): each file holds a few books,
each book a title and pages. The format and the control codes follow the
Sky SC book editor (BookEditor.Wpf: BookParser.cs, BookSerializer.cs), which
read them from the game's render_text_book.

On the platform a page is plain text: the line break is a new line, the text
codes stay as they are ("#3S" size, "#72x#210y#920F" illustration, "#F" no
illustration), the colour byte 0x07 n is written {Cn}, an item name 0x1F n is
{In}, and the other control bytes {xNN}. Pages are separated by 0x03 in the
file. Turning a page back into bytes gives the original bytes exactly.
"""
from __future__ import annotations

import re
import struct

TOKEN = re.compile(r"\{C(\d{1,3})\}|\{I(\d{1,5})\}|\{x([0-9A-Fa-f]{2})\}")
# t_book00 is a stub without books (no title table).
FILES = tuple(f"t_book{number:02d}" for number in range(1, 17))


class BookError(RuntimeError):
    pass


def entries(data: bytes) -> list[tuple[int, int]]:
    """(title offset, content offset) of each book, in file order."""
    found = []
    index, end = 0, len(data)
    while index + 4 <= len(data) and index < end:
        title, content = struct.unpack_from("<HH", data, index)
        if not (4 <= title < content < len(data)):
            raise BookError(f"en-tête de livre invalide à 0x{index:X}")
        end = min(end, title)
        found.append((title, content))
        index += 4
    return sorted(found)


def _content_end(data: bytes, start: int) -> int:
    """End of a book's content: its terminating NUL (codes and SJIS skipped)."""
    index = start
    while index < len(data):
        byte = data[index]
        if byte == 0:
            return index
        if byte == 0x07:
            index += 2
        elif byte == 0x1F:
            index += 3
        elif 0x81 <= byte <= 0x9F or 0xE0 <= byte <= 0xFC:
            index += 2
        else:
            index += 1
    raise BookError(f"livre sans fin à 0x{start:X}")


def decode_page(core, raw: bytes) -> str:
    output = []
    index, text_start = 0, 0

    def flush(until: int) -> None:
        if until > text_start:
            output.append(core.decode_game_text(raw[text_start:until]))

    while index < len(raw):
        byte = raw[index]
        if 0x81 <= byte <= 0x9F or 0xE0 <= byte <= 0xFC:
            index += 2
            continue
        if byte == 0x07 or byte == 0x1F or (byte < 0x20 and byte != 0x01):
            flush(index)
            if byte == 0x07:
                output.append(f"{{C{raw[index + 1]}}}")
                index += 2
            elif byte == 0x1F:
                output.append(f"{{I{struct.unpack_from('<H', raw, index + 1)[0]}}}")
                index += 3
            else:
                output.append(f"{{x{byte:02X}}}")
                index += 1
            text_start = index
            continue
        index += 1
    flush(len(raw))
    return "".join(output)


def encode_page(core, text: str) -> bytes:
    output = bytearray()
    cursor = 0
    for match in TOKEN.finditer(text):
        output += core.encode_game_text(text[cursor:match.start()])
        if match.group(1) is not None:
            output += bytes((0x07, int(match.group(1))))
        elif match.group(2) is not None:
            output += b"\x1f" + struct.pack("<H", int(match.group(2)))
        else:
            value = int(match.group(3), 16)
            if value in (0x00, 0x01, 0x03) or value >= 0x20:
                raise BookError(f"code {{x{match.group(3)}}} interdit dans une page")
            output.append(value)
        cursor = match.end()
    output += core.encode_game_text(text[cursor:])
    if 0 in output or 3 in output:
        raise BookError("octet NUL ou changement de page dans le texte d'une page")
    return bytes(output)


def read(core, data: bytes) -> list[dict]:
    """Each book: title and pages, as platform text."""
    books = []
    for title, content in entries(data):
        end = _content_end(data, content)
        pages = []
        start = content
        index = content
        while index <= end:
            if index == end or data[index] == 0x03:
                pages.append(decode_page(core, data[start:index]))
                start = index + 1
                index += 1
                continue
            byte = data[index]
            index += 3 if byte == 0x1F else 2 if byte == 0x07 or 0x81 <= byte <= 0x9F or 0xE0 <= byte <= 0xFC else 1
        books.append({"title": core.decode_game_text(data[title:data.index(0, title)]), "pages": pages})
    return books


def write(core, books: list[dict]) -> bytes:
    """A book file from titles and pages (same order as read() gave them)."""
    header = bytearray(4 * len(books))
    body = bytearray()
    for index, book in enumerate(books):
        title_at = len(header) + len(body)
        body += core.encode_game_text(book["title"]) + b"\0"
        content_at = len(header) + len(body)
        if content_at > 0xFFFF:
            raise BookError("fichier de livres trop grand (plus de 65 535 octets)")
        struct.pack_into("<HH", header, index * 4, title_at, content_at)
        body += b"\x03".join(encode_page(core, page) for page in book["pages"]) + b"\0"
    if len(header) + len(body) > 0x10000:
        raise BookError("fichier de livres trop grand (plus de 65 536 octets)")
    return bytes(header + body)
