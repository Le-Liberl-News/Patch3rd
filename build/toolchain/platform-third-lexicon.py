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
from typing import Iterable


class ThirdLexiconError(RuntimeError):
    pass


def load_core(path: Path):
    spec = importlib.util.spec_from_file_location("patchsc_lexique_dt", path)
    if spec is None or spec.loader is None:
        raise ThirdLexiconError(f"Moteur Lexique PatchSC illisible : {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def section_map(document: dict) -> dict[str, list[dict]]:
    sections = document.get("lexicon_sections")
    if not isinstance(sections, list):
        raise ThirdLexiconError("Export sans lexicon_sections")
    result: dict[str, list[dict]] = {}
    for section in sections:
        key = str(section.get("stable_key", ""))
        entries = section.get("entries")
        if not key or not isinstance(entries, list) or key in result:
            raise ThirdLexiconError("Section de lexique invalide ou dupliquée")
        result[key] = entries
    return result


def translated(entry: dict) -> str:
    # Exactly the platform's text, empty included: what the game shows.
    return str(entry.get("translation_fr") or "")


def source_digest(values: list[str]) -> str:
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalized_text(value: str) -> str:
    return re.sub(
        r"[ \t\u3000]+", " ",
        value.replace("\\n", "\n").replace("\u202f", " ").replace("\u00a0", " "),
    ).strip()


def by_external(entries: Iterable[dict]) -> dict[int, dict[str, dict]]:
    result: dict[int, dict[str, dict]] = {}
    for entry in entries:
        try:
            key = int(str(entry["external_key"]))
        except (KeyError, TypeError, ValueError) as error:
            raise ThirdLexiconError("Clé externe de lexique non numérique") from error
        kind = str(entry.get("field_kind", "")).casefold()
        if not kind or kind in result.setdefault(key, {}):
            raise ThirdLexiconError(f"Entrée dupliquée : {key}/{kind}")
        result[key][kind] = entry
    return result


def compile_pair_table(core, data: bytes, entries: Iterable[dict], pointer_fields_offset: int,
                       *, embedded_ids: bool, label: str,
                       missing_entries: list[str] | None = None) -> bytes:
    parsed = core.parse_pointer_table(data, pointer_fields_offset)
    id_to_index: dict[int, int] = {}
    if embedded_ids:
        for index, pointer in enumerate(parsed.pointers):
            record = parsed.records[pointer]
            if len(record.prefix) < 2:
                raise ThirdLexiconError(f"{label}: ID embarqué absent à l'entrée {index}")
            identifier = struct.unpack_from("<H", record.prefix)[0]
            if identifier in id_to_index:
                raise ThirdLexiconError(f"{label}: ID embarqué dupliqué {identifier}")
            id_to_index[identifier] = index

    desired: dict[int, tuple[bytes, bytes]] = {}
    for external, fields in by_external(entries).items():
        index = id_to_index.get(external) if embedded_ids else external - 2
        if index is None or not 0 <= index < len(parsed.pointers):
            if missing_entries is not None:
                missing_entries.append(f"{label}/{external}")
                continue
            raise ThirdLexiconError(f"{label}: entrée {external} absente du DT")
        source = parsed.records[parsed.pointers[index]]
        name_entry = fields.get("nom")
        description_entry = fields.get("description")
        name = core.encode_game_text(translated(name_entry)) if name_entry else source.name
        description = (
            core.encode_game_text(translated(description_entry), b"\\n")
            if description_entry else source.description
        )
        desired[index] = (name, description)

    rebuilt = core._rebuild_pointer_table(parsed, desired, expected_ids_in_records=False)
    checked = core.parse_pointer_table(rebuilt, pointer_fields_offset)
    core._verify_semantic_roundtrip(parsed, checked, desired, label)
    return rebuilt


def compile_chain(core, data: bytes, entries: Iterable[dict], *, _validate: bool = True) -> bytes:
    entries = list(entries)
    if len(data) < 8:
        raise ThirdLexiconError("T_CHAIN tronqué")
    first_table, third_table = struct.unpack_from("<HH", data, 4)
    first_record = struct.unpack_from("<I", data, first_table)[0]
    third_record = struct.unpack_from("<H", data, third_table)[0]
    if first_record <= first_table or (first_record - first_table) % 8:
        raise ThirdLexiconError("Première table de T_CHAIN invalide")
    if third_record <= third_table or (third_record - third_table) % 2:
        raise ThirdLexiconError("Troisième table de T_CHAIN invalide")
    first_count = (first_record - first_table) // 8
    third_count = (third_record - third_table) // 2
    if first_count % 2:
        raise ThirdLexiconError("Première table de T_CHAIN impaire")
    per_series = first_count // 2
    interleaved = [struct.unpack_from("<I", data, first_table + i * 8)[0] for i in range(first_count)]
    records = interleaved[0::2] + interleaved[1::2] + [
        struct.unpack_from("<H", data, third_table + i * 2)[0] for i in range(third_count)
    ]
    if records != sorted(records) or len(set(records)) != len(records):
        raise ThirdLexiconError("Ordre des enregistrements de T_CHAIN invalide")

    source: list[bytes] = []
    metadata: list[bytes] = []
    for index, record in enumerate(records):
        limit = third_table if index == first_count - 1 else (records[index + 1] if index + 1 < len(records) else len(data))
        text, end = core._cstring(data, record + 8, limit)
        if end != limit:
            raise ThirdLexiconError(f"T_CHAIN: fin inattendue à l'entrée {index}")
        source.append(text)
        metadata.append(data[record + 2:record + 8])

    desired = list(source)
    for external, fields in by_external(entries).items():
        index = external - 2
        if not 0 <= index < len(desired):
            raise ThirdLexiconError(f"Chain: entrée {external} absente du DT")
        entry = fields.get("réplique") or fields.get("terme")
        if entry:
            desired[index] = core.encode_game_text(translated(entry))

    output = bytearray(data[:records[0]])
    new_records: list[int] = []

    def append_record(index: int) -> int:
        position = len(output)
        text_offset = position + 8
        if text_offset > 0xFFFF:
            raise ThirdLexiconError("T_CHAIN dépasse la limite u16")
        output.extend(struct.pack("<H", text_offset))
        output.extend(metadata[index])
        output.extend(desired[index])
        output.append(0)
        return position

    for index in range(first_count):
        new_records.append(append_record(index))
    new_third_table = len(output)
    struct.pack_into("<H", output, 6, new_third_table)
    output.extend(b"\0" * (third_count * 2))
    for index in range(first_count, first_count + third_count):
        new_records.append(append_record(index))
    for character in range(per_series):
        for series in range(2):
            struct.pack_into("<I", output, first_table + (character * 2 + series) * 8,
                             new_records[series * per_series + character])
    for index in range(third_count):
        struct.pack_into("<H", output, new_third_table + index * 2, new_records[first_count + index])
    rebuilt = bytes(output)
    if len(rebuilt) > 0x10000:
        raise ThirdLexiconError("T_CHAIN dépasse la limite u16")
    # Reparse through the same function with source values to validate the layout.
    if _validate and compile_chain(core, rebuilt, [], _validate=False) != rebuilt:
        raise ThirdLexiconError("Roundtrip T_CHAIN différent")
    return rebuilt


CONTROL = re.compile(r"<x([0-9a-fA-F]{1,2})>")


def encode_control_text(core, value: str) -> bytes:
    output = bytearray()
    cursor = 0
    for match in CONTROL.finditer(value):
        output.extend(core.encode_game_text(value[cursor:match.start()]))
        output.append(int(match.group(1), 16))
        cursor = match.end()
    output.extend(core.encode_game_text(value[cursor:]))
    return bytes(output)


def pointer_segments(data: bytes, table_offset: int) -> tuple[list[int], list[bytes]]:
    first = struct.unpack_from("<H", data, table_offset)[0]
    if first <= table_offset or (first - table_offset) % 2:
        raise ThirdLexiconError("Table de chaînes contiguë invalide")
    count = (first - table_offset) // 2
    pointers = list(struct.unpack_from(f"<{count}H", data, table_offset))
    unique = sorted(set(pointers))
    if not unique or unique[0] < table_offset + count * 2 or unique[-1] >= len(data):
        raise ThirdLexiconError("Pointeur de chaîne hors limites")
    limits = {pointer: unique[i + 1] if i + 1 < len(unique) else len(data) for i, pointer in enumerate(unique)}
    return pointers, [data[pointer:limits[pointer]] for pointer in pointers]


def compile_fish(core, data: bytes, entries: Iterable[dict]) -> bytes:
    if len(data) < 10:
        raise ThirdLexiconError("T_FISH tronqué")
    table_offset = struct.unpack_from("<H", data, 8)[0]
    pointers, source = pointer_segments(data, table_offset)
    desired = list(source)
    for external, fields in by_external(entries).items():
        index = external - 2
        if not 0 <= index < len(desired):
            raise ThirdLexiconError(f"Poissons: entrée {external} absente du DT")
        entry = fields.get("terme")
        if not entry:
            continue
        trailing = len(source[index]) - len(source[index].rstrip(b"\0"))
        if trailing < 1:
            raise ThirdLexiconError(f"Poissons: terminateur absent à l'entrée {external}")
        desired[index] = encode_control_text(core, translated(entry)) + b"\0" * trailing
    header = bytearray(data[:table_offset] + b"\0" * (len(desired) * 2))
    offsets: dict[bytes, int] = {}
    for index, value in enumerate(desired):
        offset = offsets.get(value)
        if offset is None:
            offset = len(header)
            offsets[value] = offset
            header.extend(value)
        if offset > 0xFFFF:
            raise ThirdLexiconError("T_FISH dépasse la limite u16")
        struct.pack_into("<H", header, table_offset + index * 2, offset)
    rebuilt = bytes(header)
    if len(rebuilt) > 0x10000:
        raise ThirdLexiconError("T_FISH dépasse la limite u16")
    checked_pointers, checked = pointer_segments(rebuilt, table_offset)
    if checked != desired or len(checked_pointers) != len(pointers):
        raise ThirdLexiconError("Roundtrip T_FISH incorrect")
    return rebuilt


def compile_town(core, data: bytes, entries: Iterable[dict]) -> bytes:
    count = struct.unpack_from("<H", data)[0]
    pointers = list(struct.unpack_from(f"<{count}H", data, 2))
    source: list[tuple[bytes, int]] = []
    for pointer in pointers:
        text, end = core._cstring(data, pointer, len(data))
        kind = data[end] if text and end < len(data) else 0
        source.append((text, kind))
    desired = [text for text, _ in source]
    for external, fields in by_external(entries).items():
        index = external - 1
        if not 0 <= index < count:
            raise ThirdLexiconError(f"Lieux: entrée {external} absente du DT")
        entry = fields.get("terme")
        if entry:
            desired[index] = core.encode_game_text(translated(entry))
    output = bytearray(struct.pack("<H", count) + b"\0" * (count * 2))
    offsets: dict[tuple[bytes, int], int] = {}
    for index, text in enumerate(desired):
        record = (text, source[index][1])
        offset = offsets.get(record)
        if offset is None:
            offset = len(output)
            offsets[record] = offset
            output.extend(text)
            output.append(0)
            if text:
                output.append(record[1])
        struct.pack_into("<H", output, 2 + index * 2, offset)
    rebuilt = bytes(output)
    if len(rebuilt) > 0x10000:
        raise ThirdLexiconError("T_TOWN dépasse la limite u16")
    return rebuilt


def compile_memo(core, data: bytes, entries: Iterable[dict]) -> bytes:
    count = struct.unpack_from("<H", data)[0] // 2
    _, prefixes, source = core._parse_fixed_single_text_records(data, count, 4, "T_MEMO")
    desired = list(source)
    for external, fields in by_external(entries).items():
        index = external - 2
        if not 0 <= index < count:
            raise ThirdLexiconError(f"Memo: entrée {external} absente du DT")
        entry = fields.get("terme")
        if entry:
            desired[index] = core.encode_game_text(translated(entry))
    return core._compile_fixed_single_text_records(data, desired, count, 4, "T_MEMO")


def compile_names(core, data: bytes, entries: Iterable[dict]) -> bytes:
    count = struct.unpack_from("<H", data)[0]
    pointers = list(struct.unpack_from(f"<{count}H", data, 2))
    requested = by_external(entries)
    maximum = max((external - 2 for external in requested), default=-1)
    fields: list[int] = []
    source: list[bytes] = []
    for index, record in enumerate(pointers[:maximum + 1]):
        limit = pointers[index + 1]
        candidates: list[tuple[int, bytes]] = []
        for field in range(record, limit - 1, 2):
            text_offset = struct.unpack_from("<H", data, field)[0]
            if text_offset != field + 2 or text_offset >= limit:
                continue
            text, end = core._cstring(data, text_offset, limit)
            if end == limit:
                candidates.append((field, text))
        if len(candidates) != 1:
            raise ThirdLexiconError(f"Noms: {len(candidates)} texte(s) à l'entrée {index}")
        field, text = candidates[0]
        fields.append(field)
        source.append(text)
    desired = list(source)
    for external, values in requested.items():
        index = external - 2
        entry = values.get("nom")
        if entry:
            desired[index] = core.encode_game_text(translated(entry))
    marker = b"LIBERLNEWS_THIRD_CHARACTER_NAMES\0"
    old_marker = data.find(marker)
    base = bytearray(data[:old_marker] if old_marker >= 0 else data)
    pool_start = len(base) + len(marker)
    pool, offsets = core._build_suffix_pool(desired, pool_start)
    if len(fields) != len(desired):  # zip(strict=True) needs Python 3.10; the server runs 3.9
        raise ValueError(f"t_name._dt : {len(fields)} champs pour {len(desired)} noms")
    for field, text in zip(fields, desired):
        struct.pack_into("<H", base, field, offsets[text])
    rebuilt = bytes(base) + marker + pool
    if len(rebuilt) > 0x10000:
        raise ThirdLexiconError("T_NAME dépasse la limite u16")
    return rebuilt


def compile_shops(core, data: bytes, entries: Iterable[dict]) -> bytes:
    count = struct.unpack_from("<H", data)[0] // 2
    pointers = struct.unpack_from(f"<{count}H", data)
    records = [pointer for slot, pointer in enumerate(pointers)
               if pointer and pointer + 18 <= len(data) and struct.unpack_from("<H", data, pointer)[0] == slot]
    requested = by_external(entries)
    if len(records) != len(requested):
        raise ThirdLexiconError(f"Commerces: {len(records)} structures pour {len(requested)} lignes")
    source = [core._cstring(data, struct.unpack_from("<H", data, record + 16)[0], len(data))[0]
              for record in records]
    desired = list(source)
    for ordinal, external in enumerate(sorted(requested)):
        entry = requested[external].get("terme")
        if entry:
            desired[ordinal] = core.encode_game_text(translated(entry))
    marker = b"LIBERLNEWS_THIRD_SHOP_NAMES\0"
    old_marker = data.find(marker)
    base = bytearray(data[:old_marker] if old_marker >= 0 else data)
    pool_start = len(base) + len(marker)
    pool, offsets = core._build_suffix_pool(desired, pool_start)
    if len(records) != len(desired):  # zip(strict=True) needs Python 3.10; the server runs 3.9
        raise ValueError(f"t_shop._dt : {len(records)} boutiques pour {len(desired)} noms")
    for record, text in zip(records, desired):
        struct.pack_into("<H", base, record + 16, offsets[text])
    rebuilt = bytes(base) + marker + pool
    if len(rebuilt) > 0x10000:
        raise ThirdLexiconError("T_SHOP dépasse la limite u16")
    return rebuilt


def parse_title(data: bytes) -> tuple[list[int], list[list[bytes]]]:
    starts = list(struct.unpack_from("<4H", data))
    groups: list[list[bytes]] = []
    for start in starts:
        first = struct.unpack_from("<H", data, start)[0]
        if first <= start or (first - start) % 2:
            raise ThirdLexiconError("Sous-table T_TITLE invalide")
        count = (first - start) // 2
        pointers = struct.unpack_from(f"<{count}H", data, start)
        groups.append([data[pointer:data.find(b"\0", pointer)] for pointer in pointers])
    return starts, groups


def compile_titles(core, data: bytes, entries: Iterable[dict]) -> bytes:
    _, groups = parse_title(data)
    desired = [list(group) for group in groups]
    flattened = [(group, index) for group, values in enumerate(desired) for index in range(len(values))]
    for external, fields in by_external(entries).items():
        index = external - 2
        if not 0 <= index < len(flattened):
            raise ThirdLexiconError(f"Titres: entrée {external} absente du DT")
        entry = fields.get("titre")
        if entry:
            group, local = flattened[index]
            desired[group][local] = core.encode_game_text(translated(entry))
    output = bytearray(b"\0" * 8)
    for group_index, values in enumerate(desired):
        start = len(output)
        struct.pack_into("<H", output, group_index * 2, start)
        table = len(output)
        output.extend(b"\0" * (len(values) * 2))
        offsets: dict[bytes, int] = {}
        for index, value in enumerate(values):
            offset = offsets.get(value)
            if offset is None:
                offset = len(output)
                offsets[value] = offset
                output.extend(value)
                output.append(0)
            struct.pack_into("<H", output, table + index * 2, offset)
    rebuilt = bytes(output)
    if [len(group) for group in parse_title(rebuilt)[1]] != [len(group) for group in groups]:
        raise ThirdLexiconError("Roundtrip T_TITLE incorrect")
    return rebuilt


def parse_monster_note(data: bytes) -> list[tuple[int, bytes]]:
    records: list[tuple[int, bytes]] = []
    seen: set[int] = set()
    position = 0
    while True:
        if position + 8 > len(data):
            raise ThirdLexiconError("MNSNOTE2 tronqué")
        identifier, size = struct.unpack_from("<II", data, position)
        position += 8
        if identifier == 0xFFFFFFFF and size == 0xFFFFFFFF:
            if position != len(data) or not records:
                raise ThirdLexiconError("Terminateur MNSNOTE2 invalide")
            return records
        if identifier >> 16 != 0x30 or identifier in seen or size == 0:
            raise ThirdLexiconError(f"Enregistrement MNSNOTE2 invalide : {identifier:#x}")
        end = position + size
        if end > len(data):
            raise ThirdLexiconError("Payload MNSNOTE2 tronqué")
        seen.add(identifier)
        records.append((identifier, data[position:end]))
        position = end


def serialize_monster_note(records: list[tuple[int, bytes]]) -> bytes:
    output = bytearray()
    for identifier, payload in records:
        output.extend(struct.pack("<II", identifier, len(payload)))
        output.extend(payload)
    output.extend(b"\xff" * 8)
    rebuilt = bytes(output)
    if parse_monster_note(rebuilt) != records:
        raise ThirdLexiconError("Roundtrip MNSNOTE2 incorrect")
    return rebuilt


def monster_translations(core, entries: Iterable[dict]) -> dict[int, object]:
    rows = by_external(entries)
    result: dict[int, object] = {}
    for external, fields in rows.items():
        description = fields.get("description")
        if description is None:
            continue
        name = fields.get("nom", {})
        sources = [
            str(name.get("source_jp") or ""), str(name.get("source_en") or ""),
            str(description.get("source_jp") or ""), str(description.get("source_en") or ""),
        ]
        digest = source_digest(sources)
        translation = core.MonsterTranslation(
            digest, *sources,
            translated(name) if name else None,
            translated(description),
        )
        result[external] = translation
    return result


def compile_monster_note(core, data: bytes, entries: Iterable[dict], mapping_path: Path) -> tuple[bytes, dict[str, int]]:
    manifest = json.loads(mapping_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or not isinstance(manifest.get("mappings"), dict) \
            or not isinstance(manifest.get("ignored"), dict):
        raise ThirdLexiconError("Mapping Ennemis 3rd invalide")
    mappings = {int(key, 16): int(value) for key, value in manifest["mappings"].items()}
    ignored = {int(key, 16): str(value) for key, value in manifest["ignored"].items()}
    translations = monster_translations(core, entries)
    records = parse_monster_note(data)
    record_ids = {identifier for identifier, _ in records}
    known_ids = set(mappings) | set(ignored)
    if record_ids - known_ids or set(mappings) & set(ignored):
        raise ThirdLexiconError("Couverture du mapping MNSNOTE2 incomplète")
    changed = 0
    output: list[tuple[int, bytes]] = []
    for identifier, payload in records:
        external = mappings.get(identifier)
        if external is None:
            if ignored[identifier] == "empty":
                try:
                    _, name, description = core.parse_monster_tail(payload)
                except core.LexiqueDtError:
                    name = description = b""
                if normalized_text(core.decode_game_text(name)) \
                        or normalized_text(core.decode_game_text(description)):
                    raise ThirdLexiconError(f"MNSNOTE2 {identifier:#x} n'est plus vide")
            output.append((identifier, payload))
            continue
        translation = translations.get(external)
        if translation is None:
            raise ThirdLexiconError(f"Ligne Ennemis disparue de la plateforme : {external}")
        _, current_name, current_description = core.parse_monster_tail(payload)
        observed = (
            normalized_text(core.decode_game_text(current_name)),
            normalized_text(core.decode_game_text(current_description)),
        )
        expected_english = (
            normalized_text(translation.english_name),
            normalized_text(translation.english_description),
        )
        expected_japanese = (
            normalized_text(translation.japanese_name),
            normalized_text(translation.japanese_description),
        )
        source_field_matches = (
            observed[0] and observed[0] in (expected_english[0], expected_japanese[0])
        ) or (
            observed[1] and observed[1] in (expected_english[1], expected_japanese[1])
        )
        if observed not in (expected_english, expected_japanese) and not source_field_matches:
            raise ThirdLexiconError(
                f"MNSNOTE2 {identifier:#x}: source JP/EN différente du lexique"
            )
        rebuilt = core.compile_monster(payload, translation)
        changed += rebuilt != payload
        output.append((identifier, rebuilt))
    return serialize_monster_note(output), {"records": len(records), "changed_records": changed}


def compile_all(core, base: Path, output: Path, document: dict) -> dict[str, object]:
    sections = section_map(document)
    output.mkdir(parents=True, exist_ok=True)
    missing_entries: list[str] = []
    tasks = {
        "t_ittxt._dt": lambda data: compile_pair_table(core, data, sections["items-1"], 4, embedded_ids=True, label="Items 1", missing_entries=missing_entries),
        "t_ittxt2._dt": lambda data: compile_pair_table(core, data, sections["items-2"], 4, embedded_ids=True, label="Items 2", missing_entries=missing_entries),
        "t_magic._dt": lambda data: compile_pair_table(core, data, sections["art-et-craft"], 28, embedded_ids=False, label="Art & Craft"),
        "t_cook2._dt": lambda data: compile_pair_table(core, data, sections["cuisine"], 42, embedded_ids=False, label="Cuisine"),
        "t_chain._dt": lambda data: compile_chain(core, data, sections["chain"]),
        "t_fish._dt": lambda data: compile_fish(core, data, sections["poissons"]),
        "t_town._dt": lambda data: compile_town(core, data, sections["lieux"]),
        "t_memo._dt": lambda data: compile_memo(core, data, sections["memo"]),
        "t_name._dt": lambda data: compile_names(core, data, sections["noms"]),
        "t_shop._dt": lambda data: compile_shops(core, data, sections["commerces"]),
        "t_title._dt": lambda data: compile_titles(core, data, sections["titres"]),
    }
    changed = 0
    for filename, compiler in tasks.items():
        candidates = [path for path in base.iterdir()
                      if path.is_file() and path.name.casefold().replace(" ", "") == filename]
        if len(candidates) != 1:
            raise ThirdLexiconError(f"Source Lexique absente ou ambiguë : {filename}")
        source = candidates[0]
        original = source.read_bytes()
        rebuilt = compiler(original)
        (output / filename).write_bytes(rebuilt)
        changed += rebuilt != original
    return {"files": len(tasks), "changed_files": changed, "missing_entries": missing_entries}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile le Lexique Sky 3rd avec le moteur PatchSC.")
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--monster-base", type=Path)
    parser.add_argument("--monster-output", type=Path)
    parser.add_argument("--monster-mapping", type=Path)
    arguments = parser.parse_args()
    if arguments.export.suffix.casefold() == ".gz":
        with gzip.open(arguments.export, "rt", encoding="utf-8") as stream:
            document = json.load(stream)
    else:
        document = json.loads(arguments.export.read_text(encoding="utf-8"))
    core = load_core(arguments.core)
    report = compile_all(core, arguments.base, arguments.output, document)
    monster_arguments = [arguments.monster_base, arguments.monster_output, arguments.monster_mapping]
    if any(monster_arguments) and not all(monster_arguments):
        raise ThirdLexiconError("Les trois chemins MNSNOTE2 sont requis ensemble")
    if all(monster_arguments):
        sections = section_map(document)
        rebuilt, monster_report = compile_monster_note(
            core, arguments.monster_base.read_bytes(), sections["ennemis"], arguments.monster_mapping
        )
        arguments.monster_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.monster_output.write_bytes(rebuilt)
        report["monster_note"] = monster_report
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
