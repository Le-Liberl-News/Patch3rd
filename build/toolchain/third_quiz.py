"""The 3rd's quiz tables (ED6_DT22/t_quiz01-04._dt): reading and rebuilding.

A table of u16 record offsets, then for each question a record: two words
(question number, index of the right answer from 0) and six u16 pointers
(question, four answers, explanation), followed by its texts. The rebuild
keeps the words, writes each record's texts after it and reads them back.

Control bytes other than the line break (0x02 ends an explanation) are shown
as {x02}; a translation must keep the same ones in the same order.
"""
from __future__ import annotations

import re
import struct

FILES = ("t_quiz01", "t_quiz02", "t_quiz03", "t_quiz04")
FIELDS = ("question", "réponse 1", "réponse 2", "réponse 3", "réponse 4", "explication")
CONTROL = re.compile(r"\{x([0-9A-Fa-f]{2})\}")


class QuizError(RuntimeError):
    pass


def read(data: bytes) -> list[tuple[int, int, list[bytes]]]:
    first = struct.unpack_from("<H", data, 0)[0]
    if first == 0 or first % 2 or first > len(data):
        raise QuizError(f"table du quiz invalide (0x{first:X})")
    questions = []
    for offset in struct.unpack_from(f"<{first // 2}H", data, 0):
        if offset + 16 > len(data):
            raise QuizError(f"question hors du fichier à 0x{offset:X}")
        number, answer, *pointers = struct.unpack_from("<8H", data, offset)
        texts = []
        for pointer in pointers:
            end = data.find(0, pointer)
            if end < 0:
                raise QuizError(f"texte sans fin à 0x{pointer:X}")
            texts.append(data[pointer:end])
        questions.append((number, answer, texts))
    return questions


def rebuild(data: bytes, replace) -> bytes:
    """The table with each text passed through `replace(question, field, raw) -> raw`."""
    questions = read(data)
    output = bytearray(len(questions) * 2)
    expected = []
    for index, (number, answer, texts) in enumerate(questions):
        record = len(output)
        struct.pack_into("<H", output, index * 2, record)
        new_texts = [replace(index, field, raw) for field, raw in enumerate(texts)]
        expected.append((number, answer, new_texts))
        output += struct.pack("<2H", number, answer) + bytes(12)
        places: dict[bytes, int] = {}
        for field, text in enumerate(new_texts):
            if 0 in text:
                raise QuizError(f"question {index}, {FIELDS[field]} : octet NUL")
            if text not in places:
                places[text] = len(output)
                output += text + b"\0"
            struct.pack_into("<H", output, record + 4 + field * 2, places[text])
        if len(output) > 0xFFFF:
            raise QuizError("table du quiz trop grande (pointeur hors u16)")
    if read(bytes(output)) != expected:
        raise QuizError("relecture du quiz différente")
    return bytes(output)


def to_text(core, raw: bytes) -> str:
    """Game bytes as shown on the platform."""
    parts = re.split(rb"([\x02-\x1f])", raw)
    return "".join(
        f"{{x{part[0]:02X}}}" if len(part) == 1 and 2 <= part[0] < 0x20 else core.decode_game_text(part)
        for part in parts if part
    )


def to_bytes(core, text: str) -> bytes:
    output = bytearray()
    cursor = 0
    for match in CONTROL.finditer(text):
        output += core.encode_game_text(text[cursor:match.start()])
        value = int(match.group(1), 16)
        if not 2 <= value < 0x20:
            raise QuizError(f"code {{x{match.group(1)}}} interdit")
        output.append(value)
        cursor = match.end()
    return bytes(output + core.encode_game_text(text[cursor:]))
