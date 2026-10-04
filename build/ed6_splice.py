"""Rebuilds an ED6 archive (.dir/.dat) from The 3rd's English base with some
entries replaced, block by block: nothing is extracted and only one block is
in memory at a time.

The replaced entries are compressed by ed6-archive itself, in a small archive
holding only them (same compression mode as the English entry, or none for
fonts), then copied into the new archive with every untouched block of the
base. The layout follows `ed6-archive create`: blocks in entry order, the
offset table of the DAT header in step with the DIR. The archive can be
written straight into the patch zip, so it never takes room on the disk.
"""
from __future__ import annotations

import hashlib
import struct
import zipfile
from pathlib import Path
from typing import BinaryIO, Callable, Sequence

DIR_MAGIC = b"LB DIR\x1a\0"
DAT_MAGIC = b"LB DAT\x1a\0"
EMPTY_NAME = b"/_______.___"
ENTRY = struct.Struct("<12sIIIIII")  # name, unknown1, size, unknown2, reserved, timestamp, offset


class SpliceError(RuntimeError):
    pass


class Entry:
    __slots__ = ("name", "unknown1", "size", "unknown2", "reserved", "timestamp", "offset")

    def __init__(self, name: bytes, unknown1: int, size: int, unknown2: int, reserved: int, timestamp: int, offset: int):
        self.name, self.unknown1, self.size, self.unknown2 = name, unknown1, size, unknown2
        self.reserved, self.timestamp, self.offset = reserved, timestamp, offset

    @property
    def empty(self) -> bool:
        return self.name == EMPTY_NAME

    @property
    def allocation(self) -> int:
        return max(self.size, self.reserved)

    def file_name(self) -> str:
        text = self.name.decode("cp932").lower()
        stem, _, extension = text.partition(".")
        stem, extension = stem.rstrip(" "), extension.rstrip(" ")
        return f"{stem}.{extension}" if extension else stem

    def pack(self) -> bytes:
        return ENTRY.pack(self.name, self.unknown1, self.size, self.unknown2, self.reserved, self.timestamp, self.offset)


def read_dir_bytes(data: bytes, label: str) -> list[Entry]:
    if data[:8] != DIR_MAGIC:
        raise SpliceError(f"{label} : signature DIR invalide")
    count = struct.unpack_from("<Q", data, 8)[0]
    if len(data) != 16 + count * ENTRY.size:
        raise SpliceError(f"{label} : taille DIR incohérente")
    return [Entry(*ENTRY.unpack_from(data, 16 + index * ENTRY.size)) for index in range(count)]


def read_dir(path: Path) -> list[Entry]:
    return read_dir_bytes(path.read_bytes(), path.name)


def dir_bytes(entries: Sequence[Entry]) -> bytes:
    return DIR_MAGIC + struct.pack("<Q", len(entries)) + b"".join(entry.pack() for entry in entries)


class Dat:
    """A DAT read in place, whole or stored in parts (GitHub's file size limit)."""

    def __init__(self, parts: Sequence[Path]):
        self.parts = [(path, path.stat().st_size) for path in parts]
        self.handles = [path.open("rb") for path, _ in self.parts]

    def read(self, offset: int, length: int) -> bytes:
        out = bytearray()
        start = 0
        for handle, (_, size) in zip(self.handles, self.parts):
            if length and offset < start + size:
                local = offset - start
                take = min(length, size - local)
                handle.seek(local)
                out += handle.read(take)
                offset += take
                length -= take
            start += size
        if length:
            raise SpliceError("bloc au-delà de la fin du DAT")
        return bytes(out)

    def close(self) -> None:
        for handle in self.handles:
            handle.close()


def layout(entries: list[Entry]) -> list[int]:
    """Places the blocks as `ed6-archive create` does (entry order, right after
    the offset table); sets each offset, returns the DAT table."""
    count = len(entries)
    offsets = [0] * (count + 1)
    position = 16 + 4 * (count + 1)
    for index, entry in enumerate(entries):
        if entry.empty:
            entries[index] = Entry(EMPTY_NAME, 0, 0, 0, 0, 0, 0)
            continue
        entry.offset = offsets[index] = position
        position += entry.allocation
        offsets[index + 1] = position
    return offsets


def write_dat(out: BinaryIO, entries: list[Entry], block: Callable[[int], bytes]) -> None:
    """Writes the DAT in one pass (a zip member cannot seek back); `block(index)`
    gives the allocation of each non-empty entry."""
    offsets = layout(entries)
    out.write(DAT_MAGIC + struct.pack("<Q", len(entries)) + struct.pack(f"<{len(offsets)}I", *offsets))
    for index, entry in enumerate(entries):
        if entry.empty:
            continue
        data = block(index)
        if len(data) != entry.allocation:
            raise SpliceError(f"{entry.file_name()} : bloc de {len(data)} octets, {entry.allocation} attendus")
        out.write(data)


def write_archive(dir_path: Path, dat_path: Path, entries: list[Entry], block: Callable[[int], bytes]) -> None:
    with dat_path.open("wb") as out:
        write_dat(out, entries, block)
    dir_path.write_bytes(dir_bytes(entries))


def check(name: str, entries: Sequence[Entry], replaced: dict[int, bytes], dat: BinaryIO) -> None:
    """Reads the DAT once from the start: its table matches the DIR and every
    replaced block is where the DIR says."""
    header = dat.read(16 + 4 * (len(entries) + 1))
    table = struct.unpack_from(f"<{len(entries) + 1}I", header, 16)
    for index, entry in enumerate(entries):
        if not entry.empty and table[index] != entry.offset:
            raise SpliceError(f"{name} : table DAT différente du DIR pour {entry.file_name()}")
    position = len(header)
    for index in sorted(replaced):
        entry = entries[index]
        while position < entry.offset:
            skipped = dat.read(min(1 << 20, entry.offset - position))
            if not skipped:
                raise SpliceError(f"{name} : DAT tronqué")
            position += len(skipped)
        data = dat.read(entry.size)
        position += len(data)
        if hashlib.sha256(data).digest() != hashlib.sha256(replaced[index]).digest():
            raise SpliceError(f"{name} : {entry.file_name()} relu différent")


def splice(archive_tool: str, base_dir: Path, base_parts: Sequence[Path], files: Sequence[Path],
           output: Path | zipfile.ZipFile, work: Path, run: Callable[[list[str]], None]) -> None:
    """Writes NAME.dir and NAME.dat, in the folder `output` or straight into the
    zip `output` (the archive then never lands on the disk): the base with
    `files` in place of the English entries of the same name. Fonts (._da) are
    stored uncompressed, as in Sky SC's patch. Each replaced block is read back."""
    name = base_dir.stem
    entries = read_dir(base_dir)
    # Where each block is in the base: the new layout changes the offsets.
    source = [entry.offset for entry in entries]
    by_name: dict[str, list[int]] = {}
    for index, entry in enumerate(entries):
        if not entry.empty:
            by_name.setdefault(entry.file_name(), []).append(index)
    targets = []
    for file in files:
        indexes = by_name.get(file.name.lower(), [])
        if len(indexes) != 1:
            raise SpliceError(f"{name} : {file.name} trouvé {len(indexes)} fois dans l'archive anglaise")
        if entries[indexes[0]].size == 0:
            raise SpliceError(f"{name} : {file.name} n'a pas de données dans l'archive anglaise")
        targets.append(indexes[0])

    base = Dat(base_parts)
    try:
        # The carrier: the English blocks of the replaced entries only, then
        # ed6-archive replaces each (compression of the English entry kept).
        work.mkdir(parents=True, exist_ok=True)
        carrier_dir, carrier_dat = work / f"{name}.dir", work / f"{name}.dat"
        carrier = [Entry(entries[index].name, entries[index].unknown1, entries[index].size, entries[index].unknown2,
                         entries[index].size, entries[index].timestamp, 0) for index in targets]
        write_archive(carrier_dir, carrier_dat, carrier,
                      lambda position: base.read(entries[targets[position]].offset, entries[targets[position]].size))
        for file in files:
            storage = ["--compression", "raw"] if file.suffix.lower() == "._da" else []
            run([archive_tool, "replace", str(carrier_dir.resolve()), str(file.resolve()), "--name", file.name, *storage])
        carrier = read_dir(carrier_dir)
        carrier_data = Dat([carrier_dat])
        try:
            replaced = {}
            for position, index in enumerate(targets):
                packed = carrier_data.read(carrier[position].offset, carrier[position].size)
                replaced[index] = packed
                entry = entries[index]
                entry.size = entry.reserved = len(packed)
                entry.timestamp = carrier[position].timestamp
        finally:
            carrier_data.close()

        def block(index: int) -> bytes:
            if index in replaced:
                return replaced[index]
            entry = entries[index]
            if entry.size == 0:
                return b"\0" * entry.allocation
            return base.read(source[index], entry.allocation)

        if isinstance(output, zipfile.ZipFile):
            with output.open(f"{name}.dat", "w", force_zip64=True) as stream:
                write_dat(stream, entries, block)
            output.writestr(f"{name}.dir", dir_bytes(entries))
        else:
            output.mkdir(parents=True, exist_ok=True)
            write_archive(output / f"{name}.dir", output / f"{name}.dat", entries, block)
    finally:
        base.close()
        for path in work.glob(f"{name}.*"):
            path.unlink(missing_ok=True)

    # Read back what was written (from the zip: one pass through the member).
    if isinstance(output, zipfile.ZipFile):
        written = read_dir_bytes(output.read(f"{name}.dir"), name)
        with output.open(f"{name}.dat") as stream:
            check(name, written, replaced, stream)
    else:
        written = read_dir(output / f"{name}.dir")
        with (output / f"{name}.dat").open("rb") as stream:
            check(name, written, replaced, stream)
