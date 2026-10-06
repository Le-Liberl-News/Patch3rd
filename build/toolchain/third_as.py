"""Battle scripts of The 3rd (ED6_DT30/as*._dt): decoding, the texts they
show, and rebuilding with translated texts.

The instruction table comes from Ouroboros' ED6 3rd ASDecompiler
(github.com/Ouroboros/Falcom, ED6/ed63cn/ASDecompiler/InstructionHandler.cpp),
checked against the AS_Converter specification (0x8E0D, 0xAB) and the files:
for each opcode, the size of its operands (-1: NUL-terminated string) and
which operands are code addresses. As in that decompiler, the code is followed
from each craft entry of the file's table and along every address operand;
the bytes never reached (data, padding) are kept as they are.

A rebuild changes only the texts: every instruction keeps its opcode and
operands, the code addresses (operands and craft table) are moved by the size
difference, and the result is decoded again and compared.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

STRING = -1
# opcode: (operand sizes, indexes of the operands that are code addresses)
TABLE: dict[int, tuple[tuple[int, ...], tuple[int, ...]]] = {
    0x00: ((), ()),
    0x01: ((2,), (0,)),
    0x02: ((1, 1), ()),
    0x03: ((1, 2), ()),
    0x04: ((1, 1, 2), ()),
    0x05: ((1, 1, 4), ()),
    0x06: ((4,), ()),
    0x07: ((), ()),
    0x08: ((1, 1, 4, 4, 4), ()),
    0x09: ((1, 1, 4, 4, 4), ()),
    0x0A: ((1, 1, 1, 4), ()),
    0x0B: ((1, 1, 2), ()),
    0x0C: ((1, 1, 2, 2, 1), ()),
    0x0D: ((1, 1, 4, 4, 4, 2, 2), ()),
    0x0E: ((1, 4, 4, 4, 2, 2), ()),
    0x0F: ((2, 2), ()),
    0x10: ((2, 2), ()),
    0x11: ((1, 1, 4, 4, 4, 4, 1), ()),
    0x12: ((2, -1), ()),
    0x13: ((2,), ()),
    0x14: ((2,), ()),
    0x15: ((1, 1), ()),
    0x16: ((1, 1), ()),
    0x17: ((1, 1), ()),
    0x18: ((1, 1, 1, 2, 4, 4, 4, 2, 2, 2, 2, 2, 2, 1), ()),
    0x19: ((1, 1, -1, 2, 4, 4, 4, 2, 2, 2, 2, 2, 2, 1), ()),
    0x1A: ((1, 2), ()),
    0x1B: ((1, 1), ()),
    0x1C: ((1,), ()),
    0x1D: ((1, 1, 4), ()),
    0x1E: ((4,), ()),
    0x1F: ((2, 2, 1), ()),
    0x20: ((1, 1, 1, 1, 4, 4), ()),
    0x21: ((1, 1, 4, 4), ()),
    0x22: ((1, 1, 2, 1), (2,)),
    0x23: ((1, 1), ()),
    0x24: ((1, 1, 2), ()),
    0x25: ((1, 1, 2), ()),
    0x26: ((1, 1, 2), ()),
    0x27: ((1, 1, 2), ()),
    0x28: ((1, -1, 4), ()),
    0x29: ((1,), ()),
    0x2A: ((-1, 4), ()),
    0x2B: ((), ()),
    0x2C: ((1, 2, 2), ()),
    0x2D: ((1,), ()),
    0x2E: ((1, 4, 4, 4), ()),
    0x2F: ((1, 1), ()),
    0x31: ((1, 4), ()),
    0x32: ((1, 1), ()),
    0x33: ((1, 1), ()),
    0x34: ((), ()),
    0x35: ((1, 4, 4, 4, 4), ()),
    0x36: ((4, 4, 4, 4), ()),
    0x37: ((4, 4, 4, 4), ()),
    0x38: ((4, 4, 4, 4), ()),
    0x39: ((4, 4), ()),
    0x3A: ((4, 4), ()),
    0x3B: ((4, 4), ()),
    0x3C: ((2, 4), ()),
    0x3D: ((4, 4, 4, 4), ()),
    0x3E: ((4, 4), ()),
    0x3F: ((1,), ()),
    0x40: ((1,), ()),
    0x41: ((1,), ()),
    0x42: ((1, 4, 1), ()),
    0x43: ((1, 4, 4), ()),
    0x44: ((1, 4, 4), ()),
    0x45: ((1, 4), ()),
    0x46: ((1, 4, 4), ()),
    0x47: ((1,), ()),
    0x48: ((1, 4), ()),
    0x49: ((1, 2), ()),
    0x4A: ((1,), ()),
    0x4B: ((1, 1, 4, 2), (3,)),
    0x4C: ((2,), (0,)),
    0x4D: ((), ()),
    0x4E: ((), ()),
    0x4F: ((1, 1), ()),
    0x50: ((2,), (0,)),
    0x51: ((), ()),
    0x52: ((1,), ()),
    0x53: ((1,), ()),
    0x54: ((1,), ()),
    0x55: ((2,), ()),
    0x56: ((), ()),
    0x57: ((1, 1), ()),
    0x58: ((1,), ()),
    0x59: ((1, 2), ()),
    0x5A: ((1, 1, 4), ()),
    0x5B: ((4,), ()),
    0x5C: ((1, 4), ()),
    0x5D: ((1, 4), ()),
    0x5E: ((1,), ()),
    0x5F: ((1, 1), ()),
    0x60: ((1,), ()),
    0x61: ((4,), ()),
    0x62: ((1, 1, 1, 1, 2), ()),
    0x63: ((1, 4), ()),
    0x64: ((2,), ()),
    0x65: ((2, 1), ()),
    0x66: ((2,), ()),
    0x67: ((-1,), ()),
    0x68: ((), ()),
    0x69: ((), ()),
    0x6A: ((1, 4, 4), ()),
    0x6B: ((), ()),
    0x6C: ((), ()),
    0x6D: ((4,), ()),
    0x6E: ((4,), ()),
    0x6F: ((1, 1), ()),
    0x70: ((1, 1, 2, 2), ()),
    0x71: ((1,), ()),
    0x72: ((), ()),  # AS_Converter spec (unused in The 3rd)
    0x73: ((1,), ()),
    0x74: ((1,), ()),
    0x75: ((2,), ()),
    0x76: ((1,), ()),
    0x77: ((1,), ()),
    0x78: ((1,), ()),
    0x79: ((1,), ()),
    0x7A: ((1,), ()),
    0x7B: ((2,), ()),
    0x7C: ((1, 1), ()),
    0x7D: ((1, 4), ()),
    0x7E: ((4,), ()),
    0x7F: ((4, 4, 4, 1, 4), ()),
    0x80: ((4,), ()),
    0x81: ((1, 1, 2), ()),
    0x82: ((), ()),
    0x83: ((1,), ()),
    0x84: ((1, 2, 2, 2, 4, 1), ()),
    0x85: ((1, 1, 4), ()),
    0x86: ((2, 2, 2, 1, 4), ()),
    0x87: ((2, 1), ()),
    0x88: ((2,), ()),
    0x89: ((1,), ()),
    0x8A: ((1, 1), ()),
    0x8B: ((), ()),
    0x8C: ((), ()),
    0x8D: ((1, 4, 4, 4, 4), ()),
    0x8E: ((1, 1, -1), ()),
    0x8F: ((1,), ()),
    0x90: ((1,), ()),
    0x91: ((1,), ()),
    0x92: ((1, 1, 4, 4, 4, 2, 4), ()),
    0x93: ((1, 1, -1), ()),
    0x94: ((1, -1, 4), ()),
    0x95: ((), ()),
    0x96: ((1, -1, 2), ()),
    0x97: ((4, 2, 2), ()),
    0x98: ((1, 1, 4, 4), ()),
    0x99: ((1,), ()),
    0x9A: ((4,), ()),
    0x9B: ((1,), ()),
    0x9C: ((1,), ()),
    0x9D: ((1,), ()),
    0x9E: ((1,), ()),
    0x9F: ((1, 4), ()),
    0xA0: ((1, 4, 2, -1), ()),
    0xA1: ((1, 4), ()),
    0xA2: ((1,), ()),
    0xA3: ((1, 1), ()),
    0xA4: ((1, 2), ()),
    0xA5: ((1, 1, 4, 4, 1), ()),
    0xA6: ((1, 1, 1, 4, 4, 4, 4), ()),
    0xA7: ((1, 2), ()),
    0xA8: ((1, 1), ()),
    0xA9: ((4,), ()),
    0xAA: ((4, 4), ()),
    0xAB: ((1, 1, 1, 4), ()),  # AS_Converter spec; the files have 8-byte AB instructions
    0xAC: ((1, 1, 4, 4, 1), ()),
    0xAD: ((1, 1, 4), ()),
    0xAE: ((2, 4), ()),
    0xAF: ((1, 1, 4, 4, 4), ()),
    0xB0: ((1, 2), ()),
    0xB1: ((1, 2), ()),
}
END, GOTO, RET = 0x00, 0x01, 0x51


class AsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Operand:
    offset: int      # from the start of the instruction
    size: int        # in bytes, terminator included for strings
    kind: str        # "int", "address" or "string"


@dataclass(frozen=True)
class Instruction:
    address: int
    end: int
    opcode: int
    operands: tuple[Operand, ...]


def _sizes(data: bytes, address: int, list_lines: int | None = None) -> tuple[tuple[int, ...], tuple[int, ...]]:
    opcode = data[address]
    if opcode == 0x30 and list_lines is not None:
        # A rebuilt list read with its known number of lines: an empty line
        # (written as the platform holds it) is a line, not the end.
        return (1,) + (STRING,) * list_lines + (1,), ()
    if opcode == 0x30:
        # char_say_random: how many lines may be drawn, then the lines, ended by
        # an empty string (as04050 announces 6 lines and holds 2). Where the list
        # lacks its end, the engine reads the next bytes as one more line, never
        # drawn; so does this decoder.
        cursor, lines = address + 2, 0
        while cursor < len(data) and data[cursor] != 0:
            cursor = data.index(0, cursor) + 1
            lines += 1
        if not lines or cursor >= len(data):
            raise AsError(f"0x30 à 0x{address:X} : liste de répliques invalide")
        return (1,) + (STRING,) * lines + (1,), ()
    if opcode == 0x8E:
        # 0x8E01: a file name; 0x8E0D: five 32-bit values; others: a byte and four.
        variant = data[address + 1]
        if variant == 1:
            return (1, 1, STRING), ()
        if variant == 0x0D:
            return (1, 1, 4, 4, 4, 4, 4), ()
        return (1, 1, 4, 4, 4, 4), ()
    if opcode == 0xA4:
        return ((1, 2) if data[address + 1] == 2 else (1,)), ()
    if opcode not in TABLE:
        raise AsError(f"instruction 0x{opcode:02X} inconnue à 0x{address:X}")
    return TABLE[opcode]


def _decode_one(data: bytes, address: int, list_lines: int | None = None) -> Instruction:
    sizes, addresses = _sizes(data, address, list_lines)
    cursor = address + 1
    operands = []
    for index, size in enumerate(sizes):
        if size == STRING:
            end = data.find(0, cursor)
            if end < 0:
                raise AsError(f"texte sans fin à 0x{cursor:X}")
            size, kind = end + 1 - cursor, "string"
        else:
            kind = "address" if index in addresses else "int"
        if cursor + size > len(data):
            raise AsError(f"instruction 0x{data[address]:02X} tronquée à 0x{address:X}")
        operands.append(Operand(cursor - address, size, kind))
        cursor += size
    return Instruction(address, cursor, data[address], tuple(operands))


def craft_table(data: bytes) -> tuple[int, int, list[int]]:
    if len(data) < 8:
        raise AsError("fichier trop court")
    start, end = struct.unpack_from("<HH", data)
    if not (4 <= start <= end <= len(data)) or (end - start) % 2:
        raise AsError("table des crafts invalide")
    return start, end, [value for (value,) in struct.iter_unpack("<H", data[start:end])]


def decode(data: bytes, list_lines: dict[int, int] | None = None) -> dict[int, Instruction]:
    """Every instruction reachable from the craft table, by address.

    `list_lines` gives the number of lines of 0x30 lists by address (a rebuilt
    file, whose lists may hold empty lines)."""
    list_lines = list_lines or {}
    _, table_end, entries = craft_table(data)
    # Entries pointing into the header (before the end of the table) are not code.
    pending = [entry for entry in entries if table_end <= entry < len(data)]
    decoded: dict[int, Instruction] = {}
    while pending:
        address = pending.pop()
        while address not in decoded:
            if address >= len(data):
                raise AsError(f"code hors du fichier à 0x{address:X}")
            item = _decode_one(data, address, list_lines.get(address))
            decoded[address] = item
            for operand in item.operands:
                if operand.kind == "address":
                    target = struct.unpack_from("<H", data, address + operand.offset)[0]
                    # Below the table: an engine selector, not code (BeginThread 0xFE, 0x000C).
                    if table_end <= target < len(data):
                        pending.append(target)
            if item.opcode in (END, GOTO, RET):
                break
            address = item.end
    ordered = sorted(decoded)
    for before, after in zip(ordered, ordered[1:]):
        if decoded[before].end > after:
            raise AsError(f"instructions qui se chevauchent à 0x{before:X} et 0x{after:X}")

    # Loops reach code the address operands do not show: every stretch between
    # two reached instructions (and after the last) that decodes exactly as
    # instructions is code as well.
    for left, right in _gaps(decoded, len(data)):
        cursor, found = left, {}
        try:
            while cursor < right:
                item = _decode_one(data, cursor, list_lines.get(cursor))
                found[cursor] = item
                cursor = item.end
        except AsError:
            continue
        if cursor == right:
            decoded.update(found)
    return decoded


def _gaps(decoded: dict[int, Instruction], size: int) -> list[tuple[int, int]]:
    """Byte ranges of the code area holding no decoded instruction."""
    gaps, cursor = [], min(decoded)
    for address in sorted(decoded):
        if address > cursor:
            gaps.append((cursor, address))
        cursor = max(cursor, decoded[address].end)
    if cursor < size:
        gaps.append((cursor, size))
    return gaps


def undecoded(data: bytes, decoded: dict[int, Instruction] | None = None) -> list[tuple[int, int]]:
    """Parts of the code area that are not instructions (data or unknown forms)."""
    decoded = decode(data) if decoded is None else decoded
    return _gaps(decoded, len(data))


def texts(data: bytes, decoded: dict[int, Instruction] | None = None) -> list[tuple[str, bytes]]:
    """The strings of the code, in file order: (key "0x0123.2", raw bytes)."""
    decoded = decode(data) if decoded is None else decoded
    found = []
    for address in sorted(decoded):
        item = decoded[address]
        for index, operand in enumerate(item.operands):
            # char_say_random: only the lines that can be drawn are texts.
            if item.opcode == 0x30 and index > data[address + 1]:
                continue
            if operand.kind == "string" and operand.size > 1:
                start = address + operand.offset
                found.append((f"0x{address:04X}.{index}", data[start:start + operand.size - 1]))
    return found


def _operand_positions(raw: bytes, item: Instruction) -> list[tuple[int, int]]:
    """(offset, size) of each operand in an instruction's bytes, strings measured anew."""
    positions = []
    cursor = 1
    for operand in item.operands:
        size = raw.index(0, cursor) + 1 - cursor if operand.kind == "string" else operand.size
        positions.append((cursor, size))
        cursor += size
    return positions


def say_text(text: bytes) -> bytes:
    """Encode the standard battle-script SayText used after a VoiceOut."""
    if 0 in text:
        raise AsError("SayText to insert contains a NUL byte")
    return b"\x28\xFF" + text + b"\0" + struct.pack("<I", 1000)


def rebuild(
    data: bytes,
    replacements: dict[str, bytes],
    insertions: dict[int, bytes] | None = None,
) -> bytes:
    """Replace strings and optionally insert a SayText after VoiceOut addresses."""
    decoded = decode(data)
    start, end, entries = craft_table(data)
    insertions = insertions or {}
    if (replacements or insertions) and undecoded(data, decoded):
        # Code we cannot read may hold addresses that would not be moved.
        raise AsError("fichier dont une partie du code n'est pas décodable : textes non modifiables")
    new_bytes: dict[int, bytes] = {}
    for key, value in replacements.items():
        address_text, index_text = key.split(".")
        address, index = int(address_text, 16), int(index_text)
        item = decoded.get(address)
        if item is None or index >= len(item.operands) or item.operands[index].kind != "string":
            raise AsError(f"texte {key} introuvable")
        if 0 in value:
            raise AsError(f"texte {key} : octet NUL")
        raw = bytearray(new_bytes.get(address, data[address:item.end]))
        offset, size = _operand_positions(bytes(raw), item)[index]
        raw[offset:offset + size] = value + b"\0"
        new_bytes[address] = bytes(raw)

    for address, value in insertions.items():
        item = decoded.get(address)
        if item is None or item.opcode != 0x88:
            raise AsError(f"VoiceOut 0x{address:X} not found")
        say_text(value)

    # Segments: decoded instructions (maybe rewritten) and the bytes between them.
    cuts = sorted(set(decoded) | {item.end for item in decoded.values()} | {0, len(data)})
    moved: dict[int, int] = {}
    output = bytearray()
    for left, right in zip(cuts, cuts[1:]):
        moved[left] = len(output)
        raw = new_bytes.get(left, data[left:right])
        output += raw
        if left in insertions:
            output += say_text(insertions[left])
    moved[len(data)] = len(output)
    if len(output) > 0x10000:
        raise AsError(f"fichier trop grand après traduction ({len(output)} octets)")

    def relocate(value: int) -> int:
        if value < end or value >= len(data):
            return value
        if value not in moved:
            raise AsError(f"adresse 0x{value:X} au milieu d'une instruction")
        return moved[value]

    for address, item in decoded.items():
        raw = new_bytes.get(address, data[address:item.end])
        for operand, (offset, _) in zip(item.operands, _operand_positions(raw, item)):
            if operand.kind == "address":
                value = struct.unpack_from("<H", raw, offset)[0]
                struct.pack_into("<H", output, moved[address] + offset, relocate(value))
    if moved.get(start, start) != start:
        raise AsError("la table des crafts a bougé")
    for position, entry in enumerate(entries):
        struct.pack_into("<H", output, start + 2 * position, relocate(entry))
    result = bytes(output)

    # Check: same instructions, same non-text operands, addresses moved alike.
    again = decode(result, {moved[address]: len(item.operands) - 2
                            for address, item in decoded.items() if item.opcode == 0x30})
    if len(again) != len(decoded) + len(insertions):
        raise AsError("nombre d'instructions différent après reconstruction")
    for address, item in decoded.items():
        other = again.get(moved[address])
        if other is None or other.opcode != item.opcode or len(other.operands) != len(item.operands):
            raise AsError(f"instruction 0x{address:X} différente après reconstruction")
        for before, after in zip(item.operands, other.operands):
            old = data[address + before.offset:address + before.offset + before.size]
            new = result[other.address + after.offset:other.address + after.offset + after.size]
            if before.kind == "address":
                if relocate(struct.unpack("<H", old)[0]) != struct.unpack("<H", new)[0]:
                    raise AsError(f"adresse mal déplacée dans 0x{address:X}")
            elif before.kind == "int" and old != new:
                raise AsError(f"opérande modifié dans 0x{address:X}")
    for address, value in insertions.items():
        raw = new_bytes.get(address, data[address:decoded[address].end])
        inserted_address = moved[address] + len(raw)
        inserted = again.get(inserted_address)
        if inserted is None or inserted.opcode != 0x28:
            raise AsError(f"SayText missing after VoiceOut 0x{address:X}")
        operand = inserted.operands[1]
        actual = result[inserted.address + operand.offset:inserted.address + operand.offset + operand.size - 1]
        if actual != value:
            raise AsError(f"incorrect SayText after VoiceOut 0x{address:X}")
    return result
