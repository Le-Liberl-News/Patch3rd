"""Code changes of Sky SC's French executable, carried over to The 3rd's
(DX8 ed6_win3.exe and DX9 ed6_win3_DX9.exe).

The French accents are single bytes 0xA0-0xCA (the Japanese half-width
katakana range). The English engine measures those bytes as half-width
katakana and only knows the width of bytes 0x00-0x7F, read with a signed
byte. Sky SC's French patch (ed6_win2fr.exe):

- reads every character width from a new 256-entry table (kerning of the
  French font, accents included), indexed by an unsigned byte;
- sends 0xA0-0xDF through the same path as ASCII when measuring a line,
  starting a new line or typing a name;
- keeps 0xA0-0xDF single-byte in the character reader (no Shift-JIS pair).

The 3rd runs the same engine (the English width table is identical) at other
addresses: each change is found by its code pattern, the number of places
found must be exactly the expected one, else the build stops. Screen-specific
changes of Sky SC (casino, fishing, its texture rectangles) are not carried
over.
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from third_exe_core import PeImage

WIDTHS_FILE = Path(__file__).with_name("third-exe-widths.json")
# English width table of the 16 px font, bytes 0x20-0x7F (Sky SC and The 3rd).
ENGLISH_WIDTHS = (
    10, 6, 8, 12, 11, 15, 13, 5, 8, 7, 9, 12, 6, 8, 6, 9, 11, 9, 11, 11, 11, 11, 11, 11, 11, 11, 6, 6, 11, 12, 11, 10,
    16, 13, 12, 13, 13, 12, 11, 13, 13, 7, 9, 13, 11, 16, 13, 14, 12, 14, 12, 11, 12, 13, 13, 16, 12, 12, 12, 8, 9, 7, 11, 12,
    7, 10, 11, 10, 11, 10, 9, 11, 11, 6, 6, 11, 6, 15, 11, 11, 11, 11, 9, 9, 7, 11, 11, 14, 10, 10, 9, 8, 7, 8, 12, 10,
)


class ExeCodeError(RuntimeError):
    pass


def load_widths(path: Path = WIDTHS_FILE) -> list[int]:
    widths = json.loads(path.read_text(encoding="utf-8"))["widths"]
    if len(widths) != 256 or not all(isinstance(value, int) and 0 <= value < 64 for value in widths):
        raise ExeCodeError(f"{path.name} : 256 largeurs entières attendues")
    return widths


class _Patcher:
    def __init__(self, image: PeImage):
        self.image = image
        self.data = image.data
        text = image.sections[0]
        self.text = (text.raw_offset, text.raw_offset + text.raw_size)
        self.changes: list[tuple[int, bytes]] = []

    def find(self, pattern: bytes, expected: int, label: str) -> list[re.Match]:
        start, end = self.text
        found = list(re.finditer(pattern, bytes(self.data[start:end]), re.S))
        if len(found) != expected:
            raise ExeCodeError(f"{self.image.label} : {label} trouvé {len(found)} fois au lieu de {expected}")
        return [_Shifted(match, start) for match in found]

    def write(self, raw: int, value: bytes) -> None:
        for other, previous in self.changes:
            if raw < other + len(previous) and other < raw + len(value):
                raise ExeCodeError(f"{self.image.label} : deux modifications au même endroit (0x{raw:X})")
        self.changes.append((raw, bytes(value)))
        self.data[raw:raw + len(value)] = value

    def va(self, raw: int) -> int:
        return self.image.raw_to_va(raw)

    @staticmethod
    def rel8(source_end: int, target: int) -> bytes:
        distance = target - source_end
        if not -128 <= distance <= 127:
            raise ExeCodeError(f"saut court impossible ({distance})")
        return struct.pack("<b", distance)


class _Shifted:
    """A match whose positions are raw file offsets."""

    def __init__(self, match: re.Match, shift: int):
        self.match, self.shift = match, shift

    def start(self, group: int = 0) -> int:
        return self.match.start(group) + self.shift

    def group(self, group: int = 0) -> bytes:
        return self.match.group(group)


def _jump_target(data: bytearray, raw: int) -> int:
    """Target (raw) of the short jump at raw."""
    return raw + 2 + struct.unpack_from("<b", data, raw + 1)[0]


def _near_target(patcher: _Patcher, raw: int, length: int) -> int:
    """Target (VA) of the rel32 branch ending at raw + length."""
    return patcher.va(raw) + length + struct.unpack_from("<i", patcher.data, raw + length - 4)[0]


def _branch(source_va: int, length: int, target_va: int, opcode: bytes) -> bytes:
    return opcode + struct.pack("<i", target_va - (source_va + length))


def apply_code_patches(image: PeImage, widths: list[int] | None = None) -> dict[str, int]:
    widths = widths or load_widths()
    patcher = _Patcher(image)
    data = patcher.data
    report: dict[str, int] = {}

    # 1. Width table: the English one (0x00-0x7F) is replaced by the French one
    # (0x00-0xFF) in a new section, read with an unsigned byte.
    english = b"".join(struct.pack("<I", value) for value in [16] * 32) + b"".join(
        struct.pack("<I", value) for value in ENGLISH_WIDTHS
    )
    tables = [match.start() for match in re.finditer(re.escape(english), bytes(data))]
    if len(tables) != 1:
        raise ExeCodeError(f"{image.label} : table des largeurs anglaise trouvée {len(tables)} fois")
    old_table = image.raw_to_va(tables[0])
    readers = patcher.find(b"\\x8b\\x04." + re.escape(struct.pack("<I", old_table)), 12, "lecture de largeur")
    widening = []
    for reader in readers:
        # mov eax, [index*4 + table]: the SIB byte gives the index register.
        sib_at = reader.start() + 2
        sib = data[sib_at]
        if sib & 0xC7 != 0x85:
            raise ExeCodeError(f"{image.label} : lecture de largeur inattendue à 0x{patcher.va(sib_at):X}")
        index = (sib >> 3) & 7
        # The index comes from movsx index, r8 (0F BE, mod 11), at most 0x70 bytes before.
        candidates = [
            raw for raw in range(sib_at - 0x70, sib_at)
            if data[raw] == 0x0F and data[raw + 1] == 0xBE and data[raw + 2] >> 6 == 3 and (data[raw + 2] >> 3) & 7 == index
        ]
        if not candidates:
            raise ExeCodeError(f"{image.label} : extension de l'octet introuvable avant 0x{patcher.va(sib_at):X}")
        widening.append(candidates[-1])
    if len(set(widening)) != len(readers):
        raise ExeCodeError(f"{image.label} : extensions d'octet partagées entre lectures de largeur")

    # 2. Places measuring a character: 0xA0-0xDF goes the ASCII way (the
    # "printable ASCII" jump target) instead of being a half-width katakana.
    classify = patcher.find(
        b"\\x3c\\x5f\\x76(.)(?:.{0,24}?)\\x80\\xf9\\xa0\\x73\\x04\\xb0\\x01\\xeb\\x06\\x80\\xf9\\xe0\\x0f\\x93\\xc0",
        8, "classement des caractères",
    )
    for match in classify:
        ascii_target = _jump_target(data, match.start() + 2)
        setae = match.start() + len(match.group()) - 3
        patcher.write(setae, b"\x72" + patcher.rel8(setae + 2, ascii_target) + b"\x90")
    report["classement"] = len(classify)

    # 3. Line measure: unsigned comparison to 0x20 (accents are not control characters).
    for match in patcher.find(b"\\x8a\\x0e\\x0f\\x84....\\x80\\xf9\\x20\\x0f\\x8c", 1, "mesure de ligne"):
        patcher.write(match.start() + len(match.group()) - 1, b"\x82")
    for match in patcher.find(b"\\xe8....\\x8a\\x44\\x24\\x14\\x3c\\x20\\x7c\\x34", 1, "mesure de mot"):
        patcher.write(match.start() + len(match.group()) - 2, b"\x72")

    # 4. Code caves at the end of .text: "byte in 0xA0-0xDF: continue as a single
    # byte character, else the original jump".
    text = image.sections[0]
    cave_raw = text.raw_offset + text.virtual_size
    cave_end = text.raw_offset + min(text.raw_size, image._align(text.virtual_size, image.section_alignment))
    cave_va = patcher.va(cave_raw)
    caves = bytearray()

    def cave(register_compare_a0: bytes, register_compare_e0: bytes, original: int, resume: int) -> int:
        start = cave_va + len(caves)
        code = bytearray(register_compare_a0)
        code += _branch(start + len(code), 6, original, b"\x0f\x82")
        code += register_compare_e0
        code += _branch(start + len(code), 6, original, b"\x0f\x83")
        code += _branch(start + len(code), 5, resume, b"\xe9")
        caves.extend(code)
        return start

    # 4a. Text drawing: a byte >= 0x80 was always a Shift-JIS pair.
    (match,) = patcher.find(b"\\x3c\\x80\\x0f\\x83....\\x66\\x0f\\x6e\\x84\\x24", 1, "dessin du texte")
    jae = match.start() + 2
    target = cave(b"\x3c\xa0", b"\x3c\xe0", _near_target(patcher, jae, 6), patcher.va(jae + 6))
    patcher.write(jae, _branch(patcher.va(jae), 6, target, b"\x0f\x83"))

    # 4b. Typing a name: only 0x20-0x7F were accepted.
    (match,) = patcher.find(b"\\x8a\\x0f\\x80\\xf9\\x20\\x0f\\x82....\\x80\\xf9\\x7f\\x0f\\x87....", 1, "saisie d'un nom")
    jb, ja = match.start() + 5, match.start() + 14
    target = cave(b"\x80\xf9\xa0", b"\x80\xf9\xe0", _near_target(patcher, jb, 6), patcher.va(ja + 6))
    patcher.write(jb, _branch(patcher.va(jb), 6, target, b"\x0f\x82"))
    patcher.write(ja, _branch(patcher.va(ja), 6, target, b"\x0f\x87"))

    if cave_raw + len(caves) > cave_end or any(data[cave_raw:cave_raw + len(caves)]):
        raise ExeCodeError(f"{image.label} : pas de place libre en fin de .text pour {len(caves)} octets")
    patcher.write(cave_raw, bytes(caves))
    struct.pack_into("<I", data, text.header_offset + 8, text.virtual_size + len(caves))

    # 5. Character width of the name being typed: 0x20-0xDF accepted, 0xA0-0xDF
    # measured from the table, Shift-JIS lead bytes 0x80-0x9F refused.
    (match,) = patcher.find(
        b"\\x80\\xfb\\x7f\\x0f\\x87(....)\\x8b\\x0d....\\x8d\\x43\\xe0\\x3c\\x5f\\x76.\\x80\\xfb\\x80\\x72."
        b"\\x80\\xfb\\xa0\\x72\\x05\\x80\\xfb\\xe0\\x72.\\x66\\x0f\\x6e",
        1, "largeur d'un caractère saisi",
    )
    start = match.start()
    patcher.write(start + 2, b"\xdf")
    refuse = _near_target(patcher, start + 3, 6)
    ascii_target = _jump_target(data, start + 20)
    last_jb = start + 35
    patcher.write(last_jb + 1, patcher.rel8(last_jb + 2, ascii_target))
    lead = last_jb + 2
    patcher.write(lead, _branch(patcher.va(lead), 5, refuse, b"\xe9"))

    # 6. Character reader: 0xA0-0xDF is a single byte, not the start of a pair.
    (match,) = patcher.find(
        b"\\x80\\xf9\\x80\\x72(.)\\x80\\xf9\\xa0\\x72\\x05\\x80\\xf9\\xe0\\x72(.)\\x8d\\x42\\x01\\x89\\x07", 1, "lecture d'un caractère",
    )
    single = _jump_target(data, match.start() + 3)
    jb = match.start() + 13
    katakana = _jump_target(data, jb)
    if bytes(data[katakana:katakana + 3]) != b"\x80\xf9\x80":
        raise ExeCodeError(f"{image.label} : lecture d'un caractère inattendue")
    patcher.write(jb + 1, patcher.rel8(jb + 2, single))
    patcher.write(katakana + 2, b"\xe0")

    # 7. Line splitting: 0xA0-0xDF is one character with the ASCII width.
    (match,) = patcher.find(
        b"\\x80\\xf9\\x23\\x0f\\x84....\\x80\\xf9\\x80\\x72(.)\\x80\\xf9\\xa0\\x72\\x05\\x80\\xf9\\xe0\\x72.\\x8b\\x5c\\x24",
        1, "découpe des lignes",
    )
    first_jb = match.start() + 12
    single = _jump_target(data, first_jb)
    patcher.write(match.start() + 23, patcher.rel8(match.start() + 24, single))
    (match,) = patcher.find(b"\\xff\\x44\\x24.\\x8d\\x41\\xe0\\x8b\\x5c\\x24\\x14\\x3c\\x5f\\x77", 1, "largeur à la découpe")
    patcher.write(match.start() + len(match.group()) - 2, b"\xbf")

    # 8. Short width loop: 0xA0-0xDF measured from the table (Sky SC jumped there
    # for 0xE0 and above instead, which gave the accents a full width).
    (match,) = patcher.find(b"\\x80\\xf9\\xa0\\x72\\x05\\x80\\xf9\\xe0\\x72\\x05\\x0f\\x28\\xc3\\xeb.\\x0f\\x28\\xc4\\xeb.", 1, "boucle de largeur")
    jb = match.start() + 8
    table_read = match.start() + len(match.group())
    patcher.write(jb + 1, patcher.rel8(jb + 2, table_read))

    # 9. Centred texts computed from the length in bytes (Sky SC French values).
    (match,) = patcher.find(b"\\x2b\\xce\\xb8\\x17\\x00\\x00\\x00\\x2b\\xc1\\xb9....\\xc1\\xe0\\x02", 1, "texte centré")
    patcher.write(match.start() + 3, b"\x28")
    patcher.write(match.start() + len(match.group()) - 1, b"\x01")

    # The table and the movzx last: the reads are located on the unpatched code.
    new_table = image.add_section(b".lnfr", b"".join(struct.pack("<I", value) for value in widths), 0x40000040)
    for reader in readers:
        patcher.write(reader.start() + len(reader.group()) - 4, struct.pack("<I", new_table))
    for raw in widening:
        patcher.write(raw + 1, b"\xb6")
    report.update({"largeurs": len(readers), "octets_non_signes": len(widening), "grottes": len(caves)})
    return report
