"""The 3rd's quest table (ED6_DT22/t_quest._dt: the Doors of the Lunar,
Star… paths): reading and rebuilding with translated texts.

Same layout as Sky SC's T_QUEST (PatchSC tools/nightly-publisher/quest_dt.py):
a table of u16 record offsets, then records of 9 metadata words and 18 u16
pointers to NUL-terminated texts, then the texts. The rebuild keeps every
record's metadata, writes each distinct text once and reads everything back.
"""
from __future__ import annotations

import struct

METADATA_WORDS = 9
STRING_POINTERS = 18
RECORD_SIZE = (METADATA_WORDS + STRING_POINTERS) * 2
# Project limit of the Sky SC tables (a game buffer), kept for The 3rd.
MAX_SIZE = 65_356
# What each of the 18 texts of a record is (the others are left blank).
FIELDS = {0: "chemin", 2: "titre", 3: "lieu", 4: "inscription", 5: "conditions",
          6: "résumé", 7: "récompense", 8: "porte"}


class QuestError(RuntimeError):
    pass


def record_offsets(data: bytes) -> list[int]:
    first = struct.unpack_from("<H", data, 0)[0]
    if first == 0 or first % 2 or first > len(data):
        raise QuestError(f"table des quêtes invalide (0x{first:X})")
    offsets = list(struct.unpack_from(f"<{first // 2}H", data, 0))
    for offset in offsets:
        if offset + RECORD_SIZE > len(data):
            raise QuestError(f"quête hors du fichier à 0x{offset:X}")
    return offsets


def read(data: bytes) -> list[tuple[tuple[int, ...], list[bytes]]]:
    """Each quest: its metadata words and its 18 texts (raw bytes)."""
    quests = []
    for offset in record_offsets(data):
        metadata = struct.unpack_from(f"<{METADATA_WORDS}H", data, offset)
        texts = []
        for pointer in struct.unpack_from(f"<{STRING_POINTERS}H", data, offset + METADATA_WORDS * 2):
            end = data.find(0, pointer)
            if end < 0:
                raise QuestError(f"texte sans fin à 0x{pointer:X}")
            texts.append(data[pointer:end])
        quests.append((metadata, texts))
    return quests


def rebuild(data: bytes, replace) -> bytes:
    """The table with each text passed through `replace(raw) -> raw`."""
    quests = read(data)
    table_size = len(quests) * 2
    pool_start = table_size + len(quests) * RECORD_SIZE
    output = bytearray(pool_start)
    pool = bytearray()
    places: dict[bytes, int] = {}
    expected = []
    for index, (metadata, texts) in enumerate(quests):
        record = table_size + index * RECORD_SIZE
        struct.pack_into("<H", output, index * 2, record)
        struct.pack_into(f"<{METADATA_WORDS}H", output, record, *metadata)
        new_texts = [replace(raw) for raw in texts]
        expected.append((metadata, new_texts))
        for field, text in enumerate(new_texts):
            if 0 in text:
                raise QuestError(f"quête {index}, texte {field} : octet NUL")
            pointer = places.get(text)
            if pointer is None:
                pointer = pool_start + len(pool)
                if pointer > 0xFFFF:
                    raise QuestError("table des quêtes trop grande (pointeur hors u16)")
                places[text] = pointer
                pool += text + b"\0"
            struct.pack_into("<H", output, record + METADATA_WORDS * 2 + field * 2, pointer)
    output += pool
    if len(output) > MAX_SIZE:
        raise QuestError(f"table des quêtes traduite : {len(output)} octets, limite {MAX_SIZE}")
    if read(bytes(output)) != expected:
        raise QuestError("relecture de la table des quêtes différente")
    return bytes(output)
