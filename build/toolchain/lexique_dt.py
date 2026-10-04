from __future__ import annotations

import hashlib
import json
import random
import re
import struct
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    from googleapiclient.errors import HttpError
except ModuleNotFoundError:
    class HttpError(Exception):
        """Fallback utilisé par la compilation hors Google Sheets."""

        pass


LEXIQUE_TABLE_ID = "1fDydK9_A185s2bz9EnLEeuLosDKS7hre5NK8Zd_a-jM"
ITEM_TAB = "Items"
MAGIC_TAB = "Art & Craft"
MONSTER_TAB = "Ennemis"
CHAIN_TAB = "Chain"
FISH_TAB = "Poissons"
COOK_TAB = "Cuisine"
TOWN_TAB = "Lieux"
SHOP_TAB = "Commerces"
MEMO_TAB = "Memo"
NAME_TAB = "Noms"
RETRYABLE_GOOGLE_STATUSES = {429, 500, 502, 503, 504}
ITEM_HEADER = (
    "STATUT", "ID", "NOM VO", "NOM VA", "NOM",
    "DESC. VO", "DESC. VA", "DESCRIPTION",
)
MAGIC_HEADER = (
    "I", "NOM VO", "NOM VA", "NOM",
    "DESC. VO", "DESC. VA", "DESCRIPTION",
)
MONSTER_HEADER = ("STATUT", "VO", "VA", "VF", "DESC. VO", "DESC. VA", "DESC. VF")
CHAIN_HEADER = ("STATUT", "VO", "VA", "VF", "PERSONNAGE")
FISH_HEADER = ("STATUT", "VO", "VA", "VF")
COOK_HEADER = (
    "STATUT", "NOM VO", "NOM VA", "NOM",
    "DESC. VO", "DESC. VA", "DESCRIPTION",
)
SIMPLE_VOICE_HEADER = ("STATUT", "VO", "VA", "VF")
SHOP_HEADER = ("STATUT", "VO", "VA", "VF", "PROPOSITION")
EXPECTED_ITEM_ROWS = 1040
EXPECTED_MAGIC_ROWS = 373
EXPECTED_MONSTER_ROWS = 372
MONSTER_NAME_ONLY_SOURCE_DIGESTS = {
    # Renne (Enforcer XV): this MS entry has a name but deliberately no
    # description. It is the only name-only Ennemis row backed by an MS file.
    "d7527ecf10fec58f961d1ab4c4354d2fac69c85f6a258af0d94005c1d02aaf66",
}
EXPECTED_ITEM_ID_DIGEST = "d200fa9499763cd9687078c819fa3c050b0ee9af7a8204b3a63a1ca21c8471e4"
EXPECTED_MAGIC_SOURCE_DIGEST = "25673bad9c25065bcd21e95fe9d2ecf2f738b62b10c2540c6d9171b457e80940"
EXPECTED_CHAIN_ROWS = 48
EXPECTED_CHAIN_SOURCE_DIGEST = "dbb40cb4c7c917a4a8f46d33e5ac3d7b4258b583dcdd77bfa064f2fe2d27c5e0"
EXPECTED_FISH_ROWS = 26
EXPECTED_FISH_SOURCE_DIGEST = "f3c1f71ceb39a005c520c98215c3079c8c5f698a02142e280714fd0560aa2f5b"
EXPECTED_COOK_ROWS = 77
EXPECTED_COOK_SOURCE_DIGEST = "1e2bcec8269b74a8cd859ec88c297b20e5ae2797318a9b3230db1e1774469fbe"
EXPECTED_TOWN_ROWS = 401
EXPECTED_TOWN_SOURCE_DIGEST = "dadf35d6628db0af60297aee7349352c5aecc4147617677fb3b86a28a27f71a9"
EXPECTED_SHOP_ROWS = 127
EXPECTED_SHOP_SOURCE_DIGEST = "36a68962ddc3204b55c2298d230da86f0c010adb58a1f29683850cb63f58c10e"
EXPECTED_MEMO_ROWS = 134
EXPECTED_MEMO_SOURCE_DIGEST = "7e34fa992c5da9f6f64d0d97c9fc40fa7e0c9f02539d2fe0e1c29ee2f2c558a0"
EXPECTED_NAME_ROWS = 76
EXPECTED_NAME_SOURCE_DIGEST = "177aaa1fee113f7040f2c1d8bc6155e881ac620507a0bbfb7387db6de1adfe55"

# The Lieux sheet is arranged for translators, not in t_town's binary ID order.
# Values are 1-based binary entries; entry 0 and entries 402-404 are reserved.
# Keep this explicit: treating the sheet row as the binary ID caused the 0.5.6
# regression (for example Aéronef Cecilia became General Morgan's residence).
TOWN_SHEET_TO_BINARY_ENTRY = tuple(
    list(range(1, 120))
    + list(range(166, 170))
    + list(range(120, 166))
    + list(range(170, 230))
    + [249, 248, 247]
    + list(range(230, 247))
    + list(range(250, 402))
)
if len(TOWN_SHEET_TO_BINARY_ENTRY) != EXPECTED_TOWN_ROWS or \
        set(TOWN_SHEET_TO_BINARY_ENTRY) != set(range(1, 402)):
    raise RuntimeError("Mapping Lieux interne invalide")


class LexiqueDtError(RuntimeError):
    pass


# Bytes deliberately repurposed by the historical French SC font. This is the
# canonical mapping also used by the overlay and by the scenario injector.
FRENCH_BYTES = {
    "À": 0xC3, "Â": 0xA1, "Ä": 0xA4, "Ç": 0xA6, "È": 0xA7,
    "É": 0xA8, "Ê": 0xA9, "Ë": 0xAA, "Î": 0xAB, "Ï": 0xAC,
    "Ô": 0xAD, "Ù": 0xAE, "Û": 0xC2, "Ü": 0xB0, "à": 0xB1,
    "â": 0xB2, "ä": 0xB3, "ç": 0xB4, "è": 0xB5, "é": 0xB6,
    "ê": 0xB7, "ë": 0xB8, "î": 0xB9, "ï": 0xBA, "ô": 0xBB,
    "ù": 0xBC, "û": 0xBD, "ü": 0xBE, "ÿ": 0xBF, "Œ": 0xC0,
    "œ": 0xC1, "Ÿ": 0xC2, "°": 0xC4, "«": 0xC5, "»": 0xC6,
    "\u00a0": 0xC8, "\u202f": 0xC8, "ö": 0xC9,
    "Ö": 0xCA, "\u200a": 0xCB,
}
BYTE_TO_FRENCH = {value: key for key, value in FRENCH_BYTES.items()}
# Prefer ordinary NBSP when decoding the two source spellings sharing 0xC8.
BYTE_TO_FRENCH[0xC8] = "\u00a0"
# Older delivered files also contain these two legacy aliases. Encoding always
# uses the same C3/C2 choices as inject_structural.py.
BYTE_TO_FRENCH[0xA0] = "À"
BYTE_TO_FRENCH[0xAF] = "Û"
BYTE_TO_FRENCH[0xC7] = "★"
TEXT_REPLACEMENTS = {"…": "...", "’": "'", "–": "-", "—": "-", "♡": "㈱"}


@dataclass(frozen=True)
class TextRecord:
    offset: int
    prefix: bytes
    name: bytes
    description: bytes


@dataclass(frozen=True)
class PointerTable:
    pointers: tuple[int, ...]
    records: dict[int, TextRecord]
    pointer_fields_offset: int


@dataclass(frozen=True)
class ItemTranslation:
    item_id: int
    name: str | None
    description: str | None


@dataclass(frozen=True)
class MagicTranslation:
    magic_id: int
    name: str | None
    description: str | None


@dataclass(frozen=True)
class MonsterTranslation:
    source_digest: str
    japanese_name: str
    english_name: str
    japanese_description: str
    english_description: str
    name: str | None
    description: str | None


@dataclass(frozen=True)
class ChainTranslation:
    chain_id: int
    text: str | None


@dataclass(frozen=True)
class FishTranslation:
    fish_id: int
    description: str | None


@dataclass(frozen=True)
class CookTranslation:
    cook_id: int
    name: str | None
    description: str | None


@dataclass(frozen=True)
class IndexedTextTranslation:
    entry_id: int
    text: str | None


def _header_key(value: object) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn").strip().upper()


def _cell(row: Sequence[object], index: int) -> str:
    return str(row[index]) if index < len(row) and row[index] is not None else ""


def _translation(value: object) -> str | None:
    text = str(value) if value is not None else ""
    return text if text.strip() else None


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def parse_item_rows(rows: Sequence[Sequence[object]]) -> list[ItemTranslation]:
    if not rows or tuple(_header_key(value) for value in rows[0][:8]) != ITEM_HEADER:
        raise LexiqueDtError("Header inattendu dans l'onglet Items du Lexique")
    data = list(rows[1:])
    if len(data) != EXPECTED_ITEM_ROWS:
        raise LexiqueDtError(
            f"Items contient {len(data)} lignes de données; {EXPECTED_ITEM_ROWS} attendues"
        )
    result: list[ItemTranslation] = []
    seen: set[int] = set()
    for row_number, row in enumerate(data, 2):
        raw_id = _cell(row, 1).strip()
        if not raw_id:
            raise LexiqueDtError(f"Items ligne {row_number}: ID absent")
        try:
            item_id = int(raw_id)
        except ValueError as error:
            raise LexiqueDtError(f"Items ligne {row_number}: ID invalide {raw_id!r}") from error
        if item_id in seen:
            raise LexiqueDtError(f"Items ligne {row_number}: ID dupliqué {item_id}")
        seen.add(item_id)
        result.append(ItemTranslation(item_id, _translation(_cell(row, 4)), _translation(_cell(row, 7))))
    digest = _digest(sorted(seen))
    if digest != EXPECTED_ITEM_ID_DIGEST:
        raise LexiqueDtError(
            f"L'ensemble des IDs Items a changé ({digest}); revue du mapping requise"
        )
    return result


def parse_magic_rows(rows: Sequence[Sequence[object]]) -> list[MagicTranslation]:
    if not rows or tuple(_header_key(value) for value in rows[0][:7]) != MAGIC_HEADER:
        raise LexiqueDtError("Header inattendu dans l'onglet Art & Craft du Lexique")
    data = list(rows[1:])
    if len(data) != EXPECTED_MAGIC_ROWS:
        raise LexiqueDtError(
            f"Art & Craft contient {len(data)} lignes de données; {EXPECTED_MAGIC_ROWS} attendues"
        )
    source = [[_cell(row, index) for index in (1, 2, 4, 5)] for row in data]
    digest = _digest(source)
    if digest != EXPECTED_MAGIC_SOURCE_DIGEST:
        raise LexiqueDtError(
            f"La structure JP/EN de Art & Craft a changé ({digest}); revue du mapping requise"
        )
    return [
        MagicTranslation(index, _translation(_cell(row, 3)), _translation(_cell(row, 6)))
        for index, row in enumerate(data)
    ]


def parse_monster_rows(rows: Sequence[Sequence[object]]) -> list[MonsterTranslation]:
    if not rows or tuple(_header_key(value) for value in rows[0][:7]) != MONSTER_HEADER:
        raise LexiqueDtError("Header inattendu dans l'onglet Ennemis du Lexique")
    result: list[MonsterTranslation] = []
    seen: dict[str, tuple[str | None, str | None]] = {}
    for row in rows[1:]:
        source = [_cell(row, index) for index in (1, 2, 4, 5)]
        digest = _digest(source)
        if not any(value.strip() for value in source[2:]) and \
                digest not in MONSTER_NAME_ONLY_SOURCE_DIGESTS:
            continue
        french = (_translation(_cell(row, 3)), _translation(_cell(row, 6)))
        if digest in seen and seen[digest] != french:
            raise LexiqueDtError(
                f"Ennemis contient deux traductions françaises différentes pour la même source {digest}"
            )
        seen[digest] = french
        result.append(MonsterTranslation(digest, *source, *french))
    if len(result) != EXPECTED_MONSTER_ROWS:
        raise LexiqueDtError(
            f"Ennemis contient {len(result)} entrées MS; {EXPECTED_MONSTER_ROWS} attendues"
        )
    return result


def parse_chain_rows(rows: Sequence[Sequence[object]]) -> list[ChainTranslation]:
    if not rows or tuple(_header_key(value) for value in rows[0][:5]) != CHAIN_HEADER:
        raise LexiqueDtError("Header inattendu dans l'onglet Chain du Lexique")
    data = list(rows[1:])
    if len(data) != EXPECTED_CHAIN_ROWS:
        raise LexiqueDtError(
            f"Chain contient {len(data)} lignes de données; {EXPECTED_CHAIN_ROWS} attendues"
        )
    source = [[_cell(row, index) for index in (1, 2, 4)] for row in data]
    digest = _digest(source)
    if digest != EXPECTED_CHAIN_SOURCE_DIGEST:
        raise LexiqueDtError(
            f"La structure JP/EN/personnage de Chain a changé ({digest}); revue du mapping requise"
        )
    return [
        ChainTranslation(index, _translation(_cell(row, 3)))
        for index, row in enumerate(data)
    ]


def parse_fish_rows(rows: Sequence[Sequence[object]]) -> list[FishTranslation]:
    if not rows or tuple(_header_key(value) for value in rows[0][:4]) != FISH_HEADER:
        raise LexiqueDtError("Header inattendu dans l'onglet Poissons du Lexique")
    data = list(rows[1:])
    if len(data) != EXPECTED_FISH_ROWS:
        raise LexiqueDtError(
            f"Poissons contient {len(data)} lignes de données; {EXPECTED_FISH_ROWS} attendues"
        )
    source = [[_cell(row, index) for index in (1, 2)] for row in data]
    digest = _digest(source)
    if digest != EXPECTED_FISH_SOURCE_DIGEST:
        raise LexiqueDtError(
            f"La structure VO/VA de Poissons a changé ({digest}); revue du mapping requise"
        )
    return [
        FishTranslation(index, _translation(_cell(row, 3)))
        for index, row in enumerate(data)
    ]


def parse_cook_rows(rows: Sequence[Sequence[object]]) -> list[CookTranslation]:
    if not rows or tuple(_header_key(value) for value in rows[0][:7]) != COOK_HEADER:
        raise LexiqueDtError("Header inattendu dans l'onglet Cuisine du Lexique")
    data = list(rows[1:])
    if len(data) != EXPECTED_COOK_ROWS:
        raise LexiqueDtError(
            f"Cuisine contient {len(data)} lignes de données; {EXPECTED_COOK_ROWS} attendues"
        )
    source = [[_cell(row, index) for index in (1, 2, 4, 5)] for row in data]
    digest = _digest(source)
    if digest != EXPECTED_COOK_SOURCE_DIGEST:
        raise LexiqueDtError(
            f"La structure VO/VA de Cuisine a changé ({digest}); revue du mapping requise"
        )
    return [
        CookTranslation(
            index,
            _translation(_cell(row, 3)),
            _translation(_cell(row, 6)),
        )
        for index, row in enumerate(data)
    ]


def _parse_indexed_text_rows(
    rows: Sequence[Sequence[object]],
    label: str,
    header: tuple[str, ...],
    expected_rows: int,
    expected_digest: str,
) -> list[IndexedTextTranslation]:
    if not rows or tuple(_header_key(value) for value in rows[0][:len(header)]) != header:
        raise LexiqueDtError(f"Header inattendu dans l'onglet {label} du Lexique")
    data = list(rows[1:])
    if len(data) != expected_rows:
        raise LexiqueDtError(
            f"{label} contient {len(data)} lignes de données; {expected_rows} attendues"
        )
    source = [[_cell(row, index) for index in (1, 2)] for row in data]
    digest = _digest(source)
    if digest != expected_digest:
        raise LexiqueDtError(
            f"La structure VO/VA de {label} a changé ({digest}); revue du mapping requise"
        )
    return [
        IndexedTextTranslation(index, _translation(_cell(row, 3)))
        for index, row in enumerate(data)
    ]


def parse_town_rows(rows: Sequence[Sequence[object]]) -> list[IndexedTextTranslation]:
    return _parse_indexed_text_rows(
        rows, TOWN_TAB, SIMPLE_VOICE_HEADER, EXPECTED_TOWN_ROWS, EXPECTED_TOWN_SOURCE_DIGEST
    )


def parse_shop_rows(rows: Sequence[Sequence[object]]) -> list[IndexedTextTranslation]:
    return _parse_indexed_text_rows(
        rows, SHOP_TAB, SHOP_HEADER, EXPECTED_SHOP_ROWS, EXPECTED_SHOP_SOURCE_DIGEST
    )


def parse_memo_rows(rows: Sequence[Sequence[object]]) -> list[IndexedTextTranslation]:
    return _parse_indexed_text_rows(
        rows, MEMO_TAB, SIMPLE_VOICE_HEADER, EXPECTED_MEMO_ROWS, EXPECTED_MEMO_SOURCE_DIGEST
    )


def parse_name_rows(rows: Sequence[Sequence[object]]) -> list[IndexedTextTranslation]:
    return _parse_indexed_text_rows(
        rows, NAME_TAB, SIMPLE_VOICE_HEADER, EXPECTED_NAME_ROWS, EXPECTED_NAME_SOURCE_DIGEST
    )


def execute_google_request(request, *, attempts: int = 6, sleep=time.sleep):
    """Retry only transient Google failures; data/auth errors still fail immediately."""
    for attempt in range(1, attempts + 1):
        try:
            return request.execute()
        except HttpError as error:
            status = getattr(error.resp, "status", None)
            if status not in RETRYABLE_GOOGLE_STATUSES or attempt == attempts:
                raise
            retry_after = error.resp.get("retry-after") if error.resp else None
            try:
                delay = float(retry_after)
            except (TypeError, ValueError):
                delay = min(30.0, 2.0 ** (attempt - 1)) + random.uniform(0.0, 0.5)
            print(
                f"Google Sheets temporairement indisponible (HTTP {status}); "
                f"nouvelle tentative {attempt}/{attempts - 1} dans {delay:.1f} s."
            )
            sleep(delay)


def read_lexique(sheets, spreadsheet_id: str = LEXIQUE_TABLE_ID) -> tuple[
    list[ItemTranslation], list[MagicTranslation], list[MonsterTranslation],
    list[ChainTranslation], list[FishTranslation], list[CookTranslation]
    , list[IndexedTextTranslation], list[IndexedTextTranslation],
    list[IndexedTextTranslation], list[IndexedTextTranslation]
]:
    request = sheets.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=[
            f"'{ITEM_TAB}'!A:H",
            f"'{MAGIC_TAB.replace(chr(39), chr(39) * 2)}'!A:G",
            f"'{MONSTER_TAB}'!A:G",
            f"'{CHAIN_TAB}'!A:E",
            f"'{FISH_TAB}'!A:D",
            f"'{COOK_TAB}'!A:G",
            f"'{TOWN_TAB}'!A:D",
            f"'{SHOP_TAB}'!A:E",
            f"'{MEMO_TAB}'!A:D",
            f"'{NAME_TAB}'!A:D",
        ],
        valueRenderOption="FORMATTED_VALUE",
        majorDimension="ROWS",
    )
    response = execute_google_request(request)
    ranges = response.get("valueRanges", [])
    if len(ranges) != 10:
        raise LexiqueDtError("Réponse Google Sheets incomplète pour le Lexique")
    return (
        parse_item_rows(ranges[0].get("values", [])),
        parse_magic_rows(ranges[1].get("values", [])),
        parse_monster_rows(ranges[2].get("values", [])),
        parse_chain_rows(ranges[3].get("values", [])),
        parse_fish_rows(ranges[4].get("values", [])),
        parse_cook_rows(ranges[5].get("values", [])),
        parse_town_rows(ranges[6].get("values", [])),
        parse_shop_rows(ranges[7].get("values", [])),
        parse_memo_rows(ranges[8].get("values", [])),
        parse_name_rows(ranges[9].get("values", [])),
    )


def encode_game_text(text: str, line_break: bytes = b"\x01") -> bytes:
    # Composed form (NFC) so the glyph table sees "É", not E + U+0301.
    value = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in value:
        raise LexiqueDtError("Une traduction DT contient un octet NUL")
    output = bytearray()
    for character in value:
        if character == "\n":
            output.extend(line_break)
            continue
        replacement = TEXT_REPLACEMENTS.get(character, character)
        for normalized in replacement:
            custom = FRENCH_BYTES.get(normalized)
            if custom is not None:
                output.append(custom)
                continue
            try:
                output.extend(normalized.encode("cp932", errors="strict"))
            except UnicodeEncodeError as error:
                raise LexiqueDtError(
                    f"Caractère non encodable par le jeu: U+{ord(normalized):04X} {normalized!r}"
                ) from error
    return bytes(output)


def decode_game_text(raw: bytes, line_break: bytes = b"\x01") -> str:
    output: list[str] = []
    index = 0
    while index < len(raw):
        byte = raw[index]
        if line_break == b"\x01" and byte == 0x01:
            output.append("\n")
            index += 1
        elif byte in BYTE_TO_FRENCH:
            output.append(BYTE_TO_FRENCH[byte])
            index += 1
        elif 0x81 <= byte <= 0x9F or 0xE0 <= byte <= 0xFC:
            if index + 1 >= len(raw):
                raise LexiqueDtError("Séquence Shift-JIS tronquée dans un DT")
            try:
                output.append(raw[index:index + 2].decode("cp932", errors="strict"))
            except UnicodeDecodeError as error:
                raise LexiqueDtError("Séquence Shift-JIS invalide dans un DT") from error
            index += 2
        else:
            try:
                output.append(bytes([byte]).decode("cp932", errors="strict"))
            except UnicodeDecodeError as error:
                raise LexiqueDtError(f"Octet texte invalide dans un DT: 0x{byte:02X}") from error
            index += 1
    return "".join(output)


def _cstring(data: bytes, offset: int, limit: int) -> tuple[bytes, int]:
    if not 0 <= offset < limit <= len(data):
        raise LexiqueDtError(f"Pointeur texte DT hors limites: {offset}/{limit}")
    end = data.find(b"\0", offset, limit)
    if end < 0:
        raise LexiqueDtError(f"Chaîne DT non terminée à l'offset 0x{offset:X}")
    return data[offset:end], end + 1


def parse_pointer_table(data: bytes, pointer_fields_offset: int) -> PointerTable:
    if len(data) < 2:
        raise LexiqueDtError("DT trop court")
    table_size = struct.unpack_from("<H", data)[0]
    if table_size < 2 or table_size % 2 or table_size >= len(data):
        raise LexiqueDtError(f"Taille de table de pointeurs invalide: {table_size}")
    count = table_size // 2
    pointers = struct.unpack_from(f"<{count}H", data)
    if min(pointers) < table_size or max(pointers) >= len(data):
        raise LexiqueDtError("La table contient un pointeur d'enregistrement hors limites")
    offsets = sorted(set(pointers))
    records: dict[int, TextRecord] = {}
    for index, offset in enumerate(offsets):
        limit = offsets[index + 1] if index + 1 < len(offsets) else len(data)
        fields = offset + pointer_fields_offset
        if fields + 4 > limit:
            raise LexiqueDtError(f"Enregistrement DT tronqué à 0x{offset:X}")
        name_offset, description_offset = struct.unpack_from("<HH", data, fields)
        # Strings are held in a global pool. In stock files a record may point
        # backwards to a name or description shared with another record.
        name, _ = _cstring(data, name_offset, len(data))
        description, _ = _cstring(data, description_offset, len(data))
        records[offset] = TextRecord(
            offset=offset,
            prefix=data[offset:fields],
            name=name,
            description=description,
        )
    return PointerTable(tuple(pointers), records, pointer_fields_offset)


def _rebuild_pointer_table(
    parsed: PointerTable,
    desired: dict[int, tuple[bytes, bytes]],
    expected_ids_in_records: bool,
) -> bytes:
    count = len(parsed.pointers)
    for entry_id in desired:
        if not 0 <= entry_id < count:
            raise LexiqueDtError(f"ID DT hors limites: {entry_id} (0..{count - 1})")

    groups: dict[int, list[int]] = {}
    for entry_id, offset in enumerate(parsed.pointers):
        groups.setdefault(offset, []).append(entry_id)

    variants: list[tuple[int, bytes, bytes, TextRecord]] = []
    entry_variant: dict[int, int] = {}
    for offset in sorted(groups):
        record = parsed.records[offset]
        by_text: dict[tuple[bytes, bytes], list[int]] = {}
        for entry_id in groups[offset]:
            pair = desired.get(entry_id, (record.name, record.description))
            by_text.setdefault(pair, []).append(entry_id)
        for pair, entry_ids in sorted(by_text.items(), key=lambda item: min(item[1])):
            variant_index = len(variants)
            variants.append((min(entry_ids), pair[0], pair[1], record))
            for entry_id in entry_ids:
                entry_variant[entry_id] = variant_index

    header_size = count * 2
    output = bytearray(b"\0" * header_size)
    variant_offsets: list[int] = []
    pointer_positions: list[int] = []
    for representative_id, name, description, record in variants:
        offset = len(output)
        if offset > 0xFFFF:
            raise LexiqueDtError("Le DT traduit dépasse la limite de pointeurs u16")
        if expected_ids_in_records:
            if len(record.prefix) < 2 or struct.unpack_from("<H", record.prefix)[0] != representative_id:
                raise LexiqueDtError(
                    f"Art & Craft: l'enregistrement {representative_id} ne porte pas son ID"
                )
        output.extend(record.prefix)
        pointer_positions.append(len(output))
        output.extend(b"\0\0\0\0")
        variant_offsets.append(offset)
    values = {value for _, name, description, _ in variants for value in (name, description)}
    containers = sorted(
        (value for value in values if not any(other != value and other.endswith(value) for other in values)),
        key=lambda value: (-len(value), value),
    )
    pool: dict[bytes, int] = {}
    for container in containers:
        container_offset = len(output)
        output.extend(container)
        output.append(0)
        for value in values:
            if container.endswith(value) and value not in pool:
                pool[value] = container_offset + len(container) - len(value)
    if pool.keys() != values:
        raise LexiqueDtError("Construction interne du pool de chaînes DT incomplète")
    for variant_index, (_, name, description, _) in enumerate(variants):
        offsets = [pool[name], pool[description]]
        if max(offsets) > 0xFFFF:
            raise LexiqueDtError(
                f"Le DT traduit dépasse la limite de pointeurs u16: "
                f"offset 0x{max(offsets):X}, taille {len(output)}"
            )
        struct.pack_into("<HH", output, pointer_positions[variant_index], *offsets)
    if len(output) > 0x10000:
        raise LexiqueDtError(f"Le DT traduit fait {len(output)} octets; 65 536 maximum")
    for entry_id in range(count):
        struct.pack_into("<H", output, entry_id * 2, variant_offsets[entry_variant[entry_id]])
    return bytes(output)


def _verify_semantic_roundtrip(
    before: PointerTable,
    after: PointerTable,
    desired: dict[int, tuple[bytes, bytes]],
    label: str,
) -> None:
    if len(before.pointers) != len(after.pointers):
        raise LexiqueDtError(f"Roundtrip {label}: nombre d'enregistrements modifié")
    for entry_id in range(len(before.pointers)):
        source = before.records[before.pointers[entry_id]]
        rebuilt = after.records[after.pointers[entry_id]]
        expected = desired.get(entry_id, (source.name, source.description))
        if rebuilt.prefix != source.prefix:
            raise LexiqueDtError(f"Roundtrip {label}: métadonnées modifiées à l'ID {entry_id}")
        if (rebuilt.name, rebuilt.description) != expected:
            raise LexiqueDtError(f"Roundtrip {label}: texte incorrect à l'ID {entry_id}")


def compile_items(data: bytes, translations: Iterable[ItemTranslation]) -> bytes:
    parsed = parse_pointer_table(data, pointer_fields_offset=0)
    desired: dict[int, tuple[bytes, bytes]] = {}
    for translation in translations:
        if not 0 <= translation.item_id < len(parsed.pointers):
            raise LexiqueDtError(f"Items: ID {translation.item_id} absent de t_item2._dt")
        record = parsed.records[parsed.pointers[translation.item_id]]
        # SC deliberately leaves legacy/other-game item slots blank even though
        # t_item._dt still carries inert metadata for many of them. Populating
        # those slots both changes unused game data and can overflow this
        # format's absolute u16 string pointers.
        if not record.name and not record.description:
            continue
        line_break = b"\\n" if b"\\n" in record.description and b"\x01" not in record.description else b"\x01"
        desired[translation.item_id] = (
            encode_game_text(translation.name) if translation.name is not None else record.name,
            encode_game_text(translation.description, line_break) if translation.description is not None else record.description,
        )
    if all(
        pair == (parsed.records[parsed.pointers[entry_id]].name,
                 parsed.records[parsed.pointers[entry_id]].description)
        for entry_id, pair in desired.items()
    ):
        return data
    rebuilt = _rebuild_pointer_table(parsed, desired, expected_ids_in_records=False)
    verify = parse_pointer_table(rebuilt, pointer_fields_offset=0)
    _verify_semantic_roundtrip(parsed, verify, desired, "Items")
    return rebuilt


def compile_magic(data: bytes, translations: Iterable[MagicTranslation]) -> bytes:
    parsed = parse_pointer_table(data, pointer_fields_offset=28)
    desired: dict[int, tuple[bytes, bytes]] = {}
    translations = list(translations)
    if len(translations) > len(parsed.pointers):
        raise LexiqueDtError("Art & Craft contient plus de lignes que t_magic._dt")
    for translation in translations:
        if translation.magic_id >= len(parsed.pointers):
            raise LexiqueDtError(f"Art & Craft: ID {translation.magic_id} absent de t_magic._dt")
        record = parsed.records[parsed.pointers[translation.magic_id]]
        if len(record.prefix) < 2 or struct.unpack_from("<H", record.prefix)[0] != translation.magic_id:
            raise LexiqueDtError(
                f"Art & Craft: décalage de ligne détecté à l'ID {translation.magic_id}"
            )
        desired[translation.magic_id] = (
            encode_game_text(translation.name) if translation.name is not None else record.name,
            encode_game_text(translation.description) if translation.description is not None else record.description,
        )
    if all(
        pair == (parsed.records[parsed.pointers[entry_id]].name,
                 parsed.records[parsed.pointers[entry_id]].description)
        for entry_id, pair in desired.items()
    ):
        return data
    rebuilt = _rebuild_pointer_table(parsed, desired, expected_ids_in_records=True)
    verify = parse_pointer_table(rebuilt, pointer_fields_offset=28)
    _verify_semantic_roundtrip(parsed, verify, desired, "Art & Craft")
    return rebuilt


def compile_cook(data: bytes, translations: Sequence[CookTranslation]) -> bytes:
    parsed = parse_pointer_table(data, pointer_fields_offset=42)
    if len(translations) != EXPECTED_COOK_ROWS:
        raise LexiqueDtError(
            f"Cuisine fournit {len(translations)} traductions; {EXPECTED_COOK_ROWS} attendues"
        )
    if len(translations) > len(parsed.pointers):
        raise LexiqueDtError("Cuisine contient plus de lignes que t_cook2._dt")
    desired: dict[int, tuple[bytes, bytes]] = {}
    for expected_id, translation in enumerate(translations):
        if translation.cook_id != expected_id:
            raise LexiqueDtError(
                f"Cuisine: ID {translation.cook_id} à la position {expected_id}"
            )
        record = parsed.records[parsed.pointers[expected_id]]
        if len(record.prefix) < 2 or struct.unpack_from("<H", record.prefix)[0] != expected_id:
            raise LexiqueDtError(
                f"Cuisine: décalage de ligne détecté à l'ID {expected_id}"
            )
        desired[expected_id] = (
            encode_game_text(translation.name) if translation.name is not None else record.name,
            encode_game_text(translation.description) if translation.description is not None else record.description,
        )
    if all(
        pair == (parsed.records[parsed.pointers[entry_id]].name,
                 parsed.records[parsed.pointers[entry_id]].description)
        for entry_id, pair in desired.items()
    ):
        return data
    rebuilt = _rebuild_pointer_table(parsed, desired, expected_ids_in_records=False)
    verify = parse_pointer_table(rebuilt, pointer_fields_offset=42)
    _verify_semantic_roundtrip(parsed, verify, desired, "Cuisine")
    for entry_id in range(len(parsed.pointers)):
        record = verify.records[verify.pointers[entry_id]]
        if len(record.prefix) < 2 or struct.unpack_from("<H", record.prefix)[0] != entry_id:
            raise LexiqueDtError(
                f"Roundtrip Cuisine: ID embarqué incorrect à l'entrée {entry_id}"
            )
    return rebuilt


def _chain_layout(data: bytes) -> tuple[int, int, list[int]]:
    if len(data) < 8:
        raise LexiqueDtError("t_chain._dt tronqué")
    first_table, third_table = struct.unpack_from("<HH", data, 4)
    if first_table != 0x38 or not first_table < third_table < len(data):
        raise LexiqueDtError(
            f"Structure t_chain._dt inattendue: tables 0x{first_table:X}/0x{third_table:X}"
        )
    first_record = struct.unpack_from("<I", data, first_table)[0]
    if first_record <= first_table or (first_record - first_table) % 8:
        raise LexiqueDtError("Première table de t_chain._dt invalide")
    first_count = (first_record - first_table) // 8
    third_record = struct.unpack_from("<H", data, third_table)[0]
    if third_record <= third_table or (third_record - third_table) % 2:
        raise LexiqueDtError("Troisième table de t_chain._dt invalide")
    third_count = (third_record - third_table) // 2
    if (first_count, third_count) != (32, 16):
        raise LexiqueDtError(
            f"t_chain._dt contient {first_count}+{third_count} entrées; 32+16 attendues"
        )
    interleaved = [
        struct.unpack_from("<I", data, first_table + index * 8)[0]
        for index in range(first_count)
    ]
    third = [
        struct.unpack_from("<H", data, third_table + index * 2)[0]
        for index in range(third_count)
    ]
    # L'onglet Chain est ordonné par série, tandis que la première table du jeu
    # entrelace les deux premières séries personnage par personnage.
    ordered = interleaved[0::2] + interleaved[1::2] + third
    if len(set(ordered)) != EXPECTED_CHAIN_ROWS or ordered != sorted(ordered):
        raise LexiqueDtError("Ordre ou unicité des enregistrements de t_chain._dt invalide")
    return first_table, third_table, ordered


def parse_chain_texts(data: bytes) -> tuple[list[bytes], list[bytes]]:
    _, third_table, records = _chain_layout(data)
    texts: list[bytes] = []
    metadata: list[bytes] = []
    for index, record in enumerate(records):
        limit = (
            third_table if index == 31
            else records[index + 1] if index + 1 < len(records)
            else len(data)
        )
        if record + 8 >= limit:
            raise LexiqueDtError(f"Chain: enregistrement {index} tronqué")
        text, end = _cstring(data, record + 8, limit)
        if end != limit:
            raise LexiqueDtError(
                f"Chain: données inattendues après le texte de l'entrée {index}"
            )
        texts.append(text)
        metadata.append(data[record + 2:record + 8])
    return texts, metadata


def compile_chain(data: bytes, translations: Sequence[ChainTranslation]) -> bytes:
    first_table, _, source_records = _chain_layout(data)
    source_texts, metadata = parse_chain_texts(data)
    if len(translations) != EXPECTED_CHAIN_ROWS:
        raise LexiqueDtError(
            f"Chain fournit {len(translations)} traductions; {EXPECTED_CHAIN_ROWS} attendues"
        )
    for expected_id, translation in enumerate(translations):
        if translation.chain_id != expected_id:
            raise LexiqueDtError(
                f"Chain: ID {translation.chain_id} à la position {expected_id}"
            )
    desired = [
        encode_game_text(translation.text) if translation.text is not None else source_texts[index]
        for index, translation in enumerate(translations)
    ]

    first_record = source_records[0]
    output = bytearray(data[:first_record])
    new_records: list[int] = []

    def append_record(index: int) -> int:
        record = len(output)
        text_offset = record + 8
        if text_offset > 0xFFFF:
            raise LexiqueDtError("t_chain._dt dépasse la limite de pointeurs u16")
        output.extend(struct.pack("<H", text_offset))
        output.extend(metadata[index])
        output.extend(desired[index])
        output.append(0)
        return record

    # Première série, puis deuxième série, comme dans le stockage physique.
    for index in range(32):
        new_records.append(append_record(index))

    third_table = len(output)
    if third_table > 0xFFFF:
        raise LexiqueDtError("Table finale de t_chain._dt hors plage u16")
    struct.pack_into("<H", output, 6, third_table)
    output.extend(b"\0" * 32)
    for index in range(32, 48):
        new_records.append(append_record(index))

    if len(output) > 0x10000:
        raise LexiqueDtError(f"t_chain._dt traduit fait {len(output)} octets; 65 536 maximum")

    # La table de tête entrelace série 1 et série 2.
    for character in range(16):
        for series in range(2):
            slot = character * 2 + series
            record = new_records[series * 16 + character]
            struct.pack_into("<I", output, first_table + slot * 8, record)
    for character in range(16):
        struct.pack_into("<H", output, third_table + character * 2, new_records[32 + character])

    rebuilt = bytes(output)
    checked_texts, checked_metadata = parse_chain_texts(rebuilt)
    if checked_texts != desired:
        raise LexiqueDtError("Roundtrip Chain: textes reconstruits incorrects")
    if checked_metadata != metadata:
        raise LexiqueDtError("Roundtrip Chain: métadonnées modifiées")
    # Toutes les entrées doivent pointer vers le texte qui suit immédiatement
    # leur en-tête; c'est précisément le garde-fou qui manquait au fichier livré.
    _, _, checked_records = _chain_layout(rebuilt)
    for index, record in enumerate(checked_records):
        if struct.unpack_from("<H", rebuilt, record)[0] != record + 8:
            raise LexiqueDtError(f"Roundtrip Chain: pointeur texte invalide à l'entrée {index}")
    return rebuilt


def parse_fish_descriptions(data: bytes) -> tuple[bytes, list[bytes]]:
    if len(data) < 12:
        raise LexiqueDtError("t_fish._dt tronqué")
    pointer_offsets = [10 + 60 * index for index in range(EXPECTED_FISH_ROWS)]
    if pointer_offsets[-1] + 2 > len(data):
        raise LexiqueDtError("Table des poissons tronquée")
    description_offset = struct.unpack_from("<H", data, pointer_offsets[0])[0]
    if not 12 <= description_offset < len(data):
        raise LexiqueDtError(
            f"Début des descriptions de t_fish._dt invalide: 0x{description_offset:X}"
        )
    descriptions: list[bytes] = []
    for pointer_offset in pointer_offsets:
        position = struct.unpack_from("<H", data, pointer_offset)[0]
        if not description_offset <= position < len(data):
            raise LexiqueDtError(
                f"Pointeur de description de t_fish._dt invalide: 0x{position:X}"
            )
        end = data.find(b"\0", position)
        if end < 0:
            raise LexiqueDtError("Description non terminée dans t_fish._dt")
        descriptions.append(data[position:end])
    return data[:description_offset], descriptions


def compile_fish(data: bytes, translations: Sequence[FishTranslation]) -> bytes:
    prefix, source_descriptions = parse_fish_descriptions(data)
    if len(translations) != EXPECTED_FISH_ROWS:
        raise LexiqueDtError(
            f"Poissons fournit {len(translations)} traductions; {EXPECTED_FISH_ROWS} attendues"
        )
    desired: list[bytes] = []
    for expected_id, translation in enumerate(translations):
        if translation.fish_id != expected_id:
            raise LexiqueDtError(
                f"Poissons: ID {translation.fish_id} à la position {expected_id}"
            )
        desired.append(
            encode_game_text(translation.description, b"\\n")
            if translation.description is not None
            else source_descriptions[expected_id]
        )
    if desired == source_descriptions:
        return data
    pointer_offsets = [10 + 60 * index for index in range(EXPECTED_FISH_ROWS)]
    rebuilt = bytearray(prefix + b"".join(description + b"\0" for description in desired))
    position = len(prefix)
    if len(pointer_offsets) != len(desired):  # zip(strict=True) needs Python 3.10; the server runs 3.9
        raise ValueError(f"{len(pointer_offsets)} pointeurs pour {len(desired)} descriptions")
    for pointer_offset, description in zip(pointer_offsets, desired):
        struct.pack_into("<H", rebuilt, pointer_offset, position)
        position += len(description) + 1
    rebuilt = bytes(rebuilt)
    if len(rebuilt) > 0x10000:
        raise LexiqueDtError(f"t_fish._dt traduit fait {len(rebuilt)} octets; 65 536 maximum")
    checked_prefix, checked_descriptions = parse_fish_descriptions(rebuilt)
    static_prefix = bytearray(prefix)
    checked_static_prefix = bytearray(checked_prefix)
    for pointer_offset in pointer_offsets:
        static_prefix[pointer_offset:pointer_offset + 2] = b"\0\0"
        checked_static_prefix[pointer_offset:pointer_offset + 2] = b"\0\0"
    if checked_static_prefix != static_prefix:
        raise LexiqueDtError("Roundtrip Poissons: préfixe binaire modifié")
    if checked_descriptions != desired:
        raise LexiqueDtError("Roundtrip Poissons: descriptions reconstruites incorrectes")
    return rebuilt


def _build_suffix_pool(values: Iterable[bytes], start_offset: int) -> tuple[bytes, dict[bytes, int]]:
    unique = set(values)
    containers = sorted(
        (value for value in unique if not any(other != value and other.endswith(value) for other in unique)),
        key=lambda value: (-len(value), value),
    )
    output = bytearray()
    offsets: dict[bytes, int] = {}
    for container in containers:
        container_offset = start_offset + len(output)
        output.extend(container)
        output.append(0)
        for value in unique:
            if container.endswith(value) and value not in offsets:
                offsets[value] = container_offset + len(container) - len(value)
    if offsets.keys() != unique:
        raise LexiqueDtError("Construction du pool de chaînes incomplète")
    return bytes(output), offsets


def parse_town_records(data: bytes) -> list[tuple[bytes, int]]:
    if len(data) < 4:
        raise LexiqueDtError("t_town._dt tronqué")
    count = struct.unpack_from("<H", data)[0]
    header_size = 2 + count * 2
    if count != 405 or header_size > len(data):
        raise LexiqueDtError(f"t_town._dt contient {count} entrées; 405 attendues")
    pointers = struct.unpack_from(f"<{count}H", data, 2)
    records: list[tuple[bytes, int]] = []
    for pointer in pointers:
        text, end = _cstring(data, pointer, len(data))
        # ED6 stores one kind byte after every non-empty town name.  Omitting it
        # makes a pointer to the final record read exactly one byte past EOF.
        kind = data[end] if text and end < len(data) else 0
        if text and end >= len(data):
            raise LexiqueDtError("t_town._dt: octet kind absent après un nom")
        records.append((text, kind))
    return records


def parse_town_texts(data: bytes) -> list[bytes]:
    return [text for text, _kind in parse_town_records(data)]


def compile_town(data: bytes, translations: Sequence[IndexedTextTranslation]) -> bytes:
    source_records = parse_town_records(data)
    source = [text for text, _kind in source_records]
    if len(translations) != EXPECTED_TOWN_ROWS:
        raise LexiqueDtError(f"Lieux fournit {len(translations)} traductions; 401 attendues")
    desired = list(source)
    for expected_id, translation in enumerate(translations):
        if translation.entry_id != expected_id:
            raise LexiqueDtError(f"Lieux: ID {translation.entry_id} à la position {expected_id}")
        if translation.text is not None:
            desired[TOWN_SHEET_TO_BINARY_ENTRY[expected_id]] = encode_game_text(translation.text)
    if desired == source:
        return data
    header_size = 2 + len(desired) * 2
    output = bytearray(struct.pack("<H", len(desired)) + b"\0" * (len(desired) * 2))
    offsets: dict[tuple[bytes, int], int] = {}
    for index, text in enumerate(desired):
        kind = source_records[index][1]
        record = (text, kind)
        offset = offsets.get(record)
        if offset is None:
            offset = len(output)
            offsets[record] = offset
            output.extend(text)
            output.append(0)
            if text:
                output.append(kind)
        if offset > 0xFFFF:
            raise LexiqueDtError("t_town._dt traduit dépasse la limite u16")
        struct.pack_into("<H", output, 2 + index * 2, offset)
    if len(output) > 0x10000:
        raise LexiqueDtError("t_town._dt traduit dépasse la limite u16")
    rebuilt = bytes(output)
    rebuilt_records = parse_town_records(rebuilt)
    if [text for text, _kind in rebuilt_records] != desired or \
            [kind for _text, kind in rebuilt_records] != \
            [kind for _text, kind in source_records]:
        raise LexiqueDtError("Roundtrip Lieux: textes reconstruits incorrects")
    return rebuilt


def _parse_fixed_single_text_records(
    data: bytes, expected_count: int, prefix_size: int, label: str
) -> tuple[list[int], list[bytes], list[bytes]]:
    if len(data) < 2:
        raise LexiqueDtError(f"{label} tronqué")
    table_size = struct.unpack_from("<H", data)[0]
    if table_size % 2 or table_size // 2 != expected_count:
        raise LexiqueDtError(
            f"{label} contient {table_size // 2} entrées; {expected_count} attendues"
        )
    pointers = list(struct.unpack_from(f"<{expected_count}H", data))
    prefixes: list[bytes] = []
    texts: list[bytes] = []
    for index, record in enumerate(pointers):
        limit = pointers[index + 1] if index + 1 < len(pointers) else len(data)
        if record + prefix_size + 2 > limit:
            raise LexiqueDtError(f"{label}: entrée {index} tronquée")
        text_offset = struct.unpack_from("<H", data, record + prefix_size)[0]
        text, end = _cstring(data, text_offset, limit)
        if end != limit:
            raise LexiqueDtError(f"{label}: fin inattendue à l'entrée {index}")
        prefixes.append(data[record:record + prefix_size])
        texts.append(text)
    return pointers, prefixes, texts


def _compile_fixed_single_text_records(
    data: bytes,
    desired: Sequence[bytes],
    expected_count: int,
    prefix_size: int,
    label: str,
) -> bytes:
    _, prefixes, _ = _parse_fixed_single_text_records(data, expected_count, prefix_size, label)
    header_size = expected_count * 2
    output = bytearray(b"\0" * header_size)
    for index, (prefix, text) in enumerate(zip(prefixes, desired)):
        record = len(output)
        text_offset = record + prefix_size + 2
        if text_offset > 0xFFFF:
            raise LexiqueDtError(f"{label} dépasse la limite u16")
        struct.pack_into("<H", output, index * 2, record)
        output.extend(prefix)
        output.extend(struct.pack("<H", text_offset))
        output.extend(text)
        output.append(0)
    if len(output) > 0x10000:
        raise LexiqueDtError(f"{label} traduit dépasse 65 536 octets")
    rebuilt = bytes(output)
    _, checked_prefixes, checked_texts = _parse_fixed_single_text_records(
        rebuilt, expected_count, prefix_size, label
    )
    if checked_prefixes != prefixes or checked_texts != list(desired):
        raise LexiqueDtError(f"Roundtrip {label} incorrect")
    return rebuilt


def compile_memos(
    memo_data: bytes,
    memo1_data: bytes,
    translations: Sequence[IndexedTextTranslation],
) -> tuple[bytes, bytes]:
    if len(translations) != EXPECTED_MEMO_ROWS:
        raise LexiqueDtError(f"Memo fournit {len(translations)} traductions; 134 attendues")
    _, _, memo_source = _parse_fixed_single_text_records(memo_data, 70, 4, "t_memo._dt")
    _, _, memo1_source = _parse_fixed_single_text_records(memo1_data, 70, 4, "t_memo1._dt")
    memo_desired = list(memo_source)
    memo1_desired = list(memo1_source)
    for expected_id, translation in enumerate(translations):
        if translation.entry_id != expected_id:
            raise LexiqueDtError(f"Memo: ID {translation.entry_id} à la position {expected_id}")
        if translation.text is None:
            continue
        encoded = encode_game_text(translation.text)
        if expected_id < 64:
            memo_desired[expected_id] = encoded
        else:
            memo1_desired[expected_id - 64] = encoded
    return (
        memo_data if memo_desired == memo_source else
            _compile_fixed_single_text_records(memo_data, memo_desired, 70, 4, "t_memo._dt"),
        memo1_data if memo1_desired == memo1_source else
            _compile_fixed_single_text_records(memo1_data, memo1_desired, 70, 4, "t_memo1._dt"),
    )


NAME_POOL_MARKER = b"LIBERLNEWS_CHARACTER_NAMES\0"


def parse_name_records(data: bytes) -> tuple[list[int], list[bytes]]:
    if len(data) < 2:
        raise LexiqueDtError("t_name._dt tronqué")
    count = struct.unpack_from("<H", data)[0]
    if count != 232:
        raise LexiqueDtError(f"t_name._dt contient {count} entrées; 232 attendues")
    pointers = list(struct.unpack_from(f"<{count}H", data, 2))
    header_end = 2 + count * 2
    if pointers[0] < header_end:
        raise LexiqueDtError("t_name._dt: première entrée dans la table")
    fields: list[int] = []
    texts: list[bytes] = []
    marker_offset = data.find(NAME_POOL_MARKER)
    for index, record in enumerate(pointers[:EXPECTED_NAME_ROWS]):
        limit = pointers[index + 1]
        candidates: list[tuple[int, bytes]] = []
        for field in range(record, limit - 1, 2):
            text_offset = struct.unpack_from("<H", data, field)[0]
            inline = text_offset == field + 2 and text_offset < limit
            pooled = marker_offset >= 0 and marker_offset < text_offset < len(data)
            if not (inline or pooled):
                continue
            try:
                text, end = _cstring(data, text_offset, limit if inline else len(data))
            except LexiqueDtError:
                continue
            if (inline and end == limit) or pooled:
                candidates.append((field, text))
        if len(candidates) != 1:
            raise LexiqueDtError(
                f"t_name._dt: {len(candidates)} pointeurs texte possibles à l'entrée {index}"
            )
        field, text = candidates[0]
        fields.append(field)
        texts.append(text)
    return fields, texts


def compile_names(data: bytes, translations: Sequence[IndexedTextTranslation]) -> bytes:
    fields, source = parse_name_records(data)
    if len(translations) != EXPECTED_NAME_ROWS:
        raise LexiqueDtError(f"Noms fournit {len(translations)} traductions; 76 attendues")
    desired = list(source)
    for expected_id, translation in enumerate(translations):
        if translation.entry_id != expected_id:
            raise LexiqueDtError(f"Noms: ID {translation.entry_id} à la position {expected_id}")
        if translation.text is not None:
            desired[expected_id] = encode_game_text(translation.text)
    if desired == source:
        return data
    marker_offset = data.find(NAME_POOL_MARKER)
    base = bytearray(data[:marker_offset] if marker_offset >= 0 else data)
    pool_start = len(base) + len(NAME_POOL_MARKER)
    pool, offsets = _build_suffix_pool(desired, pool_start)
    if max(offsets.values()) > 0xFFFF or pool_start + len(pool) > 0x10000:
        raise LexiqueDtError("t_name._dt traduit dépasse 65 536 octets")
    for field, text in zip(fields, desired):
        struct.pack_into("<H", base, field, offsets[text])
    rebuilt = bytes(base) + NAME_POOL_MARKER + pool
    checked_fields, checked_texts = parse_name_records(rebuilt)
    if checked_fields != fields or checked_texts != desired:
        raise LexiqueDtError("Roundtrip Noms incorrect")
    return rebuilt


SHOP_POOL_MARKER = b"LIBERLNEWS_SHOP_NAMES\0"


def parse_shop_names(data: bytes) -> tuple[list[int], list[bytes]]:
    records: list[tuple[int, int]] = []
    for record in range(0x200, len(data) - 17):
        shop_id, _, item_count = struct.unpack_from("<HHH", data, record)
        items_offset, name_offset = struct.unpack_from("<HH", data, record + 14)
        if shop_id > 266:
            continue
        if not record + 18 <= items_offset <= len(data):
            continue
        if items_offset + item_count * 2 > len(data) or data[items_offset - 1] != 0:
            continue
        if not 0 <= name_offset < len(data):
            continue
        try:
            _cstring(data, name_offset, len(data))
        except LexiqueDtError:
            continue
        records.append((record, shop_id))
    if len(records) != EXPECTED_SHOP_ROWS or len({shop_id for _, shop_id in records}) != len(records):
        raise LexiqueDtError(
            f"t_shop._dt contient {len(records)} structures de commerce valides; 127 attendues"
        )
    names = [
        _cstring(data, struct.unpack_from("<H", data, record + 16)[0], len(data))[0]
        for record, _ in records
    ]
    return [record for record, _ in records], names


def compile_shops(data: bytes, translations: Sequence[IndexedTextTranslation]) -> bytes:
    records, source = parse_shop_names(data)
    if len(translations) != EXPECTED_SHOP_ROWS:
        raise LexiqueDtError(f"Commerces fournit {len(translations)} traductions; 127 attendues")
    desired = list(source)
    for expected_id, translation in enumerate(translations):
        if translation.entry_id != expected_id:
            raise LexiqueDtError(
                f"Commerces: ID {translation.entry_id} à la position {expected_id}"
            )
        if translation.text is not None:
            desired[expected_id] = encode_game_text(translation.text)

    if desired == source:
        return data

    marker_offset = data.find(SHOP_POOL_MARKER)
    if marker_offset >= 0:
        base = bytearray(data[:marker_offset])
    else:
        base = bytearray(data)
    pool_start = len(base) + len(SHOP_POOL_MARKER)
    pool, offsets = _build_suffix_pool(desired, pool_start)
    if max(offsets.values()) > 0xFFFF or pool_start + len(pool) > 0x10000:
        raise LexiqueDtError("t_shop._dt traduit dépasse la limite u16")
    for record, text in zip(records, desired):
        if record + 18 > len(base):
            raise LexiqueDtError("Commerces: structure située dans l'ancien pool généré")
        struct.pack_into("<H", base, record + 16, offsets[text])
    rebuilt = bytes(base) + SHOP_POOL_MARKER + pool
    checked_records, checked_names = parse_shop_names(rebuilt)
    if checked_records != records or checked_names != desired:
        raise LexiqueDtError("Roundtrip Commerces incorrect")
    return rebuilt


def _monster_extension(data: bytes) -> tuple[int, tuple[int, ...]]:
    """Locate SC's optional length-prefixed binary suffix and its references."""
    candidates: list[int] = []
    for offset in range(0x100, len(data) - 3):
        suffix_size = len(data) - offset
        if not 4 <= suffix_size <= 0x100 or data[offset - 1] != 0:
            continue
        if struct.unpack_from("<H", data, offset)[0] != suffix_size:
            continue
        # Binary metadata cannot contain a normal text run.  This prevents an
        # ordinary integer before the final strings from looking like a size.
        if re.search(rb"[\x20-\x7E]{4}", data[offset:]):
            continue
        candidates.append(offset)
    if not candidates:
        return len(data), ()
    if len(candidates) != 1:
        raise LexiqueDtError(f"DT30 bestiaire: suffixes binaires ambigus {candidates}")

    suffix_offset = candidates[0]
    encoded_offset = struct.pack("<I", suffix_offset)
    references = tuple(
        offset for offset in range(0, suffix_offset - 3)
        if data[offset:offset + 4] == encoded_offset
    )
    if not references:
        raise LexiqueDtError(
            f"DT30 bestiaire: suffixe à 0x{suffix_offset:X} sans pointeur relocalisé"
        )
    return suffix_offset, references


def _parse_monster_layout(
    data: bytes,
) -> tuple[bytes, bytes, bytes, bytes, tuple[int, ...]]:
    suffix_offset, references = _monster_extension(data)
    if suffix_offset < 3 or data[suffix_offset - 1] != 0:
        raise LexiqueDtError("DT30 bestiaire sans terminaison NUL avant le suffixe")
    description_separator = data.rfind(b"\0", 0, suffix_offset - 1)
    if description_separator < 0:
        raise LexiqueDtError("DT30 bestiaire sans séparateur de description")
    name_separator = data.rfind(b"\0", 0, description_separator)
    if name_separator < 0:
        raise LexiqueDtError("DT30 bestiaire sans séparateur de nom")
    return (
        data[:name_separator + 1],
        data[name_separator + 1:description_separator],
        data[description_separator + 1:suffix_offset - 1],
        data[suffix_offset:],
        references,
    )


def parse_monster_tail(data: bytes) -> tuple[bytes, bytes, bytes]:
    """Return the binary prefix and the final name/description strings."""
    prefix, name, description, _, _ = _parse_monster_layout(data)
    return prefix, name, description


def compile_monster(data: bytes, translation: MonsterTranslation) -> bytes:
    prefix, current_name, current_description, suffix, references = _parse_monster_layout(data)
    desired_name = encode_game_text(translation.name) if translation.name is not None else current_name
    desired_description = (
        encode_game_text(translation.description, b"\\n")
        if translation.description is not None else current_description
    )
    if (desired_name, desired_description) == (current_name, current_description):
        return data

    relocated_prefix = bytearray(prefix)
    new_suffix_offset = len(prefix) + len(desired_name) + 1 + len(desired_description) + 1
    for offset in references:
        struct.pack_into("<I", relocated_prefix, offset, new_suffix_offset)
    rebuilt = bytes(relocated_prefix) + desired_name + b"\0" + desired_description + b"\0" + suffix
    checked_prefix, checked_name, checked_description, checked_suffix, checked_references = \
        _parse_monster_layout(rebuilt)
    if checked_prefix != bytes(relocated_prefix):
        raise LexiqueDtError("Roundtrip Bestiaire: préfixe binaire modifié")
    if (checked_name, checked_description) != (desired_name, desired_description):
        raise LexiqueDtError("Roundtrip Bestiaire: chaînes terminales incorrectes")
    if checked_suffix != suffix or checked_references != references:
        raise LexiqueDtError("Roundtrip Bestiaire: suffixe binaire incorrect")
    return rebuilt


def compile_monster_files(
    source_directory: Path,
    translations: Sequence[MonsterTranslation],
    mapping_path: Path,
    destination: Path,
) -> tuple[dict[str, Path], dict[str, int]]:
    manifest = json.loads(mapping_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise LexiqueDtError("Version inconnue du mapping Bestiaire")
    mappings = manifest.get("mappings")
    ignored = manifest.get("ignored")
    if not isinstance(mappings, dict) or not isinstance(ignored, dict):
        raise LexiqueDtError("Mapping Bestiaire incomplet")
    invalid_ignored = {
        str(name): str(reason)
        for name, reason in ignored.items()
        if reason not in {"empty", "not-in-sheet"}
    }
    if invalid_ignored:
        raise LexiqueDtError(
            "Mapping Bestiaire non résolu: "
            + ", ".join(f"{name} ({reason})" for name, reason in sorted(invalid_ignored.items()))
        )
    files = {path.name.casefold(): path for path in source_directory.glob("ms*._dt")}
    mapped_names = [str(name).casefold() for names in mappings.values() if isinstance(names, list) for name in names]
    if len(mapped_names) != len(set(mapped_names)):
        raise LexiqueDtError("Un fichier apparaît plusieurs fois dans le mapping Bestiaire")
    covered = set(mapped_names) | {str(name).casefold() for name in ignored}
    if covered != files.keys():
        missing = sorted(files.keys() - covered)
        extra = sorted(covered - files.keys())
        raise LexiqueDtError(
            f"Couverture Bestiaire invalide: fichiers sans décision={missing[:20]}, absents={extra[:20]}"
        )
    by_digest: dict[str, MonsterTranslation] = {}
    for translation in translations:
        short_digest = translation.source_digest[:16]
        previous = by_digest.get(short_digest)
        if previous and previous.source_digest != translation.source_digest:
            raise LexiqueDtError(f"Collision d'empreinte Ennemis: {short_digest}")
        if previous and (previous.name, previous.description) != (translation.name, translation.description):
            raise LexiqueDtError(f"Traduction Ennemis ambiguë: {short_digest}")
        by_digest[short_digest] = translation

    destination.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}
    for source_digest, raw_names in mappings.items():
        if not isinstance(source_digest, str) or len(source_digest) != 16 or not isinstance(raw_names, list):
            raise LexiqueDtError(f"Groupe Bestiaire invalide: {source_digest!r}")
        translation = by_digest.get(source_digest)
        if translation is None:
            raise LexiqueDtError(
                f"Une source JP/EN du Bestiaire a disparu ou changé: {source_digest}"
            )
        for raw_name in raw_names:
            name = str(raw_name).casefold()
            target = destination / files[name].name
            target.write_bytes(compile_monster(files[name].read_bytes(), translation))
            outputs[name] = target

    for raw_name, reason in ignored.items():
        source = files[str(raw_name).casefold()]
        _, name, description = parse_monster_tail(source.read_bytes())
        if reason == "empty" and (name or description):
            raise LexiqueDtError(f"{source.name} n'est plus vide mais reste ignoré")
    return outputs, {"mapped": len(mapped_names), "ignored": len(ignored)}


def compile_lexique_files(
    item_source: Path,
    magic_source: Path,
    chain_source: Path,
    fish_source: Path,
    cook_source: Path,
    town_source: Path,
    shop_source: Path,
    memo_source: Path,
    memo1_source: Path,
    name_source: Path,
    item_translations: Sequence[ItemTranslation],
    magic_translations: Sequence[MagicTranslation],
    chain_translations: Sequence[ChainTranslation],
    fish_translations: Sequence[FishTranslation],
    cook_translations: Sequence[CookTranslation],
    town_translations: Sequence[IndexedTextTranslation],
    shop_translations: Sequence[IndexedTextTranslation],
    memo_translations: Sequence[IndexedTextTranslation],
    name_translations: Sequence[IndexedTextTranslation],
    monster_source: Path,
    monster_translations: Sequence[MonsterTranslation],
    monster_mapping: Path,
    destination: Path,
) -> tuple[dict[str, Path], dict[str, int]]:
    destination.mkdir(parents=True, exist_ok=True)
    outputs = {
        "ED6_DT22/t_item2._dt": destination / "t_item2._dt",
        "ED6_DT22/t_magic._dt": destination / "t_magic._dt",
        "ED6_DT22/t_chain._dt": destination / "t_chain._dt",
        "ED6_DT22/t_fish._dt": destination / "t_fish._dt",
        "ED6_DT22/t_cook2._dt": destination / "t_cook2._dt",
        "ED6_DT22/t_town._dt": destination / "t_town._dt",
        "ED6_DT22/t_shop._dt": destination / "t_shop._dt",
        "ED6_DT22/t_memo._dt": destination / "t_memo._dt",
        "ED6_DT22/t_memo1._dt": destination / "t_memo1._dt",
        "ED6_DT22/t_name._dt": destination / "t_name._dt",
    }
    outputs["ED6_DT22/t_item2._dt"].write_bytes(compile_items(item_source.read_bytes(), item_translations))
    outputs["ED6_DT22/t_magic._dt"].write_bytes(compile_magic(magic_source.read_bytes(), magic_translations))
    outputs["ED6_DT22/t_chain._dt"].write_bytes(compile_chain(chain_source.read_bytes(), chain_translations))
    outputs["ED6_DT22/t_fish._dt"].write_bytes(compile_fish(fish_source.read_bytes(), fish_translations))
    outputs["ED6_DT22/t_cook2._dt"].write_bytes(compile_cook(cook_source.read_bytes(), cook_translations))
    outputs["ED6_DT22/t_town._dt"].write_bytes(compile_town(town_source.read_bytes(), town_translations))
    outputs["ED6_DT22/t_shop._dt"].write_bytes(compile_shops(shop_source.read_bytes(), shop_translations))
    memo, memo1 = compile_memos(memo_source.read_bytes(), memo1_source.read_bytes(), memo_translations)
    outputs["ED6_DT22/t_memo._dt"].write_bytes(memo)
    outputs["ED6_DT22/t_memo1._dt"].write_bytes(memo1)
    outputs["ED6_DT22/t_name._dt"].write_bytes(compile_names(name_source.read_bytes(), name_translations))
    monster_outputs, monster_stats = compile_monster_files(
        monster_source, monster_translations, monster_mapping, destination / "monsters"
    )
    outputs.update({f"ED6_DT30/{name}": path for name, path in monster_outputs.items()})
    return outputs, monster_stats
