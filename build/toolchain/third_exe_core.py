"""PE helpers for The 3rd's executables: code pointer detection, an added
string section, retirement of replaced strings. Taken from the Sky SC
publisher (tools/nightly-publisher/exe_strings.py in PatchSC)."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Sequence


class ExeStringsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExeStringRow:
    line: int
    original: str
    translation: str
    dx8_pointer_candidates: tuple[int, ...]


@dataclass(frozen=True)
class Section:
    header_offset: int
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int


class PeImage:
    def __init__(self, data: bytes, label: str):
        self.data = bytearray(data)
        self.label = label
        if self.data[:2] != b"MZ":
            raise ExeStringsError(f"{label}: signature MZ absente")
        self.pe_offset = struct.unpack_from("<I", self.data, 0x3C)[0]
        if self.data[self.pe_offset:self.pe_offset + 4] != b"PE\0\0":
            raise ExeStringsError(f"{label}: signature PE absente")
        self.file_header = self.pe_offset + 4
        self.section_count = struct.unpack_from("<H", self.data, self.file_header + 2)[0]
        self.optional_header = self.file_header + 20
        self.optional_size = struct.unpack_from("<H", self.data, self.file_header + 16)[0]
        if struct.unpack_from("<H", self.data, self.optional_header)[0] != 0x10B:
            raise ExeStringsError(f"{label}: seul PE32 est pris en charge")
        self.image_base = struct.unpack_from("<I", self.data, self.optional_header + 28)[0]
        self.section_alignment = struct.unpack_from("<I", self.data, self.optional_header + 32)[0]
        self.file_alignment = struct.unpack_from("<I", self.data, self.optional_header + 36)[0]
        table = self.optional_header + self.optional_size
        self.sections: list[Section] = []
        for index in range(self.section_count):
            offset = table + index * 40
            virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
                "<IIII", self.data, offset + 8
            )
            self.sections.append(Section(offset, virtual_address, virtual_size, raw_offset, raw_size))
        first_raw = min(section.raw_offset for section in self.sections if section.raw_offset)
        if table + (self.section_count + 1) * 40 > first_raw:
            raise ExeStringsError(f"{label}: aucun emplacement pour une section PE supplémentaire")

    @staticmethod
    def _align(value: int, alignment: int) -> int:
        return (value + alignment - 1) // alignment * alignment

    def pointer_target(self, pointer_offset: int) -> tuple[int, bytes] | None:
        if pointer_offset < 0 or pointer_offset + 4 > len(self.data):
            return None
        address = struct.unpack_from("<I", self.data, pointer_offset)[0]
        raw = self.va_to_raw(address)
        if raw is None:
            return None
        end = self.data.find(0, raw, min(len(self.data), raw + 0x10000))
        if end < 0:
            return None
        return raw, bytes(self.data[raw:end])

    def va_to_raw(self, address: int) -> int | None:
        rva = address - self.image_base
        for section in self.sections:
            size = max(section.virtual_size, section.raw_size)
            delta = rva - section.virtual_address
            if 0 <= delta < size and delta < section.raw_size:
                return section.raw_offset + delta
        return None

    def raw_to_va(self, raw: int) -> int | None:
        for section in self.sections:
            delta = raw - section.raw_offset
            if 0 <= delta < section.raw_size:
                return self.image_base + section.virtual_address + delta
        return None

    def references_to(self, value: bytes) -> list[int]:
        needle = value + b"\0"
        references: set[int] = set()
        start = 0
        while True:
            raw = self.data.find(needle, start)
            if raw < 0:
                break
            address = self.raw_to_va(raw)
            if address is not None:
                encoded = struct.pack("<I", address)
                offset = 0
                while True:
                    found = self.data.find(encoded, offset)
                    if found < 0:
                        break
                    references.add(found)
                    offset = found + 1
            start = raw + 1
        return sorted(references)

    def reference_map(self, values: Sequence[bytes]) -> dict[bytes, list[int]]:
        """Index all requested string pointers with one linear scan of the image."""
        address_values: dict[int, set[bytes]] = {}
        for value in set(values):
            needle = value + b"\0"
            start = 0
            while True:
                raw = self.data.find(needle, start)
                if raw < 0:
                    break
                address = self.raw_to_va(raw)
                if address is not None:
                    address_values.setdefault(address, set()).add(value)
                start = raw + 1
        result: dict[bytes, set[int]] = {value: set() for value in set(values)}
        for offset in range(len(self.data) - 3):
            address = struct.unpack_from("<I", self.data, offset)[0]
            for value in address_values.get(address, ()):
                result[value].add(offset)
        return {value: sorted(offsets) for value, offsets in result.items()}

    def old_patch_area(self) -> tuple[int, int] | None:
        """Raw range of .rsrc after the real Windows resources.

        The base executables come from the manual patch 0.5.2, which stored its
        French strings (and some tables) there. Nothing of the original game
        lives in this range.
        """
        rva, size = struct.unpack_from("<II", self.data, self.optional_header + 96 + 2 * 8)
        if not rva or not size:
            return None
        section = next((item for item in self.sections if item.virtual_address == rva), None)
        if section is None:
            return None
        base = section.raw_offset
        end = 0

        def walk(offset: int) -> None:
            nonlocal end
            named, ids = struct.unpack_from("<HH", self.data, base + offset + 12)
            end = max(end, offset + 16 + 8 * (named + ids))
            for index in range(named + ids):
                name, target = struct.unpack_from("<II", self.data, base + offset + 16 + 8 * index)
                if name & 0x80000000:
                    name_offset = name & 0x7FFFFFFF
                    length = struct.unpack_from("<H", self.data, base + name_offset)[0]
                    end = max(end, name_offset + 2 + 2 * length)
                if target & 0x80000000:
                    walk(target & 0x7FFFFFFF)
                else:
                    data_rva, data_size = struct.unpack_from("<II", self.data, base + target)
                    end = max(end, target + 16, data_rva - rva + data_size)

        walk(0)
        return base + end, base + section.raw_size

    def section_of(self, raw: int) -> Section | None:
        return next((item for item in self.sections if item.raw_offset <= raw < item.raw_offset + item.raw_size), None)

    @staticmethod
    def _addressing_length(data: bytes | bytearray, modrm_at: int) -> int:
        """Bytes taken by a ModRM byte and what follows it (SIB, displacement)."""
        modrm = data[modrm_at]
        mod, rm = modrm >> 6, modrm & 7
        length = 1
        if mod != 3 and rm == 4:
            length += 1
            if mod == 0 and data[modrm_at + 1] & 7 == 5:
                length += 4
        if mod == 0 and rm == 5:
            length += 4
        elif mod == 1:
            length += 1
        elif mod == 2:
            length += 4
        return length

    def is_pointer_site(self, raw: int) -> bool:
        """Whether 4 bytes at `raw` can be an address the game really uses.

        In code, the address must be the operand of an instruction:
          * push imm32 (68), mov r32, imm32 (B8-BF);
          * mov eax/ax, [address] and back (A1, A3, 66 A1, 66 A3): the old patch
            copies some strings four bytes at a time, so these may lead into the
            middle of a string;
          * an absolute memory operand (ModRM 05, 0D … 3D) of a one byte opcode, or
            of any two byte one (0F xx: movzx, and the SSE loads movq/movups the
            old patch uses to copy eight or sixteen bytes of a string);
          * the immediate of mov r/m32, imm32 (C7), checked against the real length
            of its addressing bytes.
        Elsewhere it sits aligned in a table. Any other four bytes that happen to
        read as an address (for instance "add al, 3" gives 04 03, the high half of
        the old strings' addresses) are not references.
        """
        section = self.section_of(raw)
        if section is None:
            return False
        if section is not self.sections[0]:
            return (raw - section.raw_offset) % 4 == 0
        data = self.data
        before = data[raw - 1]
        if before == 0x68 or 0xB8 <= before <= 0xBF or before in (0xA1, 0xA3):
            return True
        if before & 0xC7 == 0x05 and (
            data[raw - 2] in (0x88, 0x89, 0x8A, 0x8B, 0x8D, 0x3B, 0x39, 0x38, 0x3A, 0xFF, 0x80, 0x81, 0x83, 0xC6, 0xC7)
            or data[raw - 3] == 0x0F
        ):
            return True
        for back in range(2, 11):
            start = raw - back
            if start >= 0 and data[start] == 0xC7 and start + 1 + self._addressing_length(data, start + 1) == raw:
                return True
        return False

    def retire_old_strings(self, replaced: dict[int, tuple[int, set[int]]]) -> tuple[int, int]:
        """Point every remaining use of the replaced old strings at the new ones, then zero them.

        `replaced` maps the raw offset of each string a translated row led to
        before the injection to its length and the new address(es) of that row's
        translation. Only strings of the old patch area are concerned. A real
        pointer (see `is_pointer_site`) still leading to the start of one of them
        is redirected to the new string; one leading into its middle, or to a
        string translated two different ways, cannot be redirected and stops the
        build. Tables and anything outside the area are never touched.
        """
        area = self.old_patch_area()
        if area is None:
            return 0, 0
        candidates = {
            raw: value for raw, value in replaced.items() if area[0] <= raw and raw + value[0] <= area[1]
        }
        owner: dict[int, int] = {}
        for raw, (length, _) in candidates.items():
            address = self.raw_to_va(raw)
            if address is not None:
                for delta in range(length + 1):
                    owner[address + delta] = raw
        redirected = 0
        problems: list[str] = []
        for shift in range(4):
            for index, (value,) in enumerate(
                struct.iter_unpack("<I", self.data[shift:len(self.data) - (len(self.data) - shift) % 4])
            ):
                raw = owner.get(value)
                site = shift + 4 * index
                if raw is None or not self.is_pointer_site(site):
                    continue
                length, targets = candidates[raw]
                delta = value - self.raw_to_va(raw)
                if delta == 0 and len(targets) == 1:
                    struct.pack_into("<I", self.data, site, next(iter(targets)))
                    redirected += 1
                else:
                    problems.append(f"0x{site:X} (+{delta})")
        if problems:
            raise ExeStringsError(
                f"{self.label}: pointeurs vers d'anciennes chaînes impossibles à rediriger : {', '.join(problems[:10])}"
            )
        for raw, (length, _) in candidates.items():
            self.data[raw:raw + length] = bytes(length)
        return redirected, len(candidates)

    def add_string_section(self, strings: Sequence[bytes]) -> dict[bytes, int]:
        unique = list(dict.fromkeys(strings))
        pool = bytearray()
        offsets: dict[bytes, int] = {}
        for value in unique:
            if b"\0" in value:
                raise ExeStringsError(f"{self.label}: traduction contenant un octet NUL")
            offsets[value] = len(pool)
            pool.extend(value)
            pool.append(0)
        virtual_address = self.add_section(b".lnstr", bytes(pool), 0x40000040)
        return {value: virtual_address + offset for value, offset in offsets.items()}

    def next_section_address(self) -> int:
        """Address (VA) the next added section will get."""
        last = max(self.sections, key=lambda section: section.virtual_address)
        return self.image_base + self._align(
            last.virtual_address + max(last.virtual_size, last.raw_size), self.section_alignment
        )

    def add_section(self, name: bytes, payload: bytes, characteristics: int) -> int:
        """Appends a section holding `payload`; returns its address (VA)."""
        table = self.optional_header + self.optional_size
        header = table + self.section_count * 40
        first_raw = min(section.raw_offset for section in self.sections if section.raw_offset)
        if header + 40 > first_raw:
            raise ExeStringsError(f"{self.label}: aucun emplacement pour une section PE supplémentaire")
        virtual_address = self.next_section_address() - self.image_base
        raw_offset = self._align(len(self.data), self.file_alignment)
        raw_size = self._align(len(payload), self.file_alignment)
        self.data.extend(b"\0" * (raw_offset - len(self.data)))
        self.data.extend(payload)
        self.data.extend(b"\0" * (raw_size - len(payload)))
        self.data[header:header + 40] = struct.pack(
            "<8sIIIIIIHHI", name.ljust(8, b"\0"), len(payload), virtual_address,
            raw_size, raw_offset, 0, 0, 0, 0, characteristics,
        )
        struct.pack_into("<H", self.data, self.file_header + 2, self.section_count + 1)
        self.sections.append(Section(header, virtual_address, len(payload), raw_offset, raw_size))
        self.section_count += 1
        struct.pack_into(
            "<I", self.data, self.optional_header + 56,
            self._align(virtual_address + len(payload), self.section_alignment),
        )
        initialized = struct.unpack_from("<I", self.data, self.optional_header + 8)[0]
        struct.pack_into("<I", self.data, self.optional_header + 8, initialized + raw_size)
        # The old checksum no longer describes the binary. Windows accepts zero for these executables.
        struct.pack_into("<I", self.data, self.optional_header + 64, 0)
        return self.image_base + virtual_address


