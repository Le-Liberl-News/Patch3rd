"""Converts the translated game images (PNG uploaded on the platform) to the
game's pictures in the format of the original (.ch: raw ARGB1555 or ARGB4444;
.ds: uncompressed DDS, ARGB8888/4444/1555, header of the original kept), for
every variant of the family: the largest from the upload, the others reduced
from it by averaging (exact when the ratio is a whole number).

Standard library only (the build server has no imaging library): a small PNG
reader (8-bit greyscale, RGB, palette, RGBA; not interlaced).
"""
from __future__ import annotations

import argparse
import base64
import gzip
import json
import struct
import sys
import zlib
from pathlib import Path


class ImageError(RuntimeError):
    pass


def read_png(data: bytes) -> tuple[int, int, list[bytearray]]:
    """Width, height, rows of RGBA bytes."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ImageError("pas un PNG")
    pos, chunks, idat = 8, {}, bytearray()
    while pos < len(data):
        length, kind = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IDAT":
            idat += body
        else:
            chunks.setdefault(kind, body)
        if kind == b"IEND":
            break
    width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", chunks[b"IHDR"])
    if depth != 8 or interlace:
        raise ImageError("PNG 8 bits par canal, non entrelacé attendu")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour)
    if channels is None:
        raise ImageError(f"type de couleur PNG {colour} non pris en charge")
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows, previous, pos = [], bytearray(stride), 0
    for _ in range(height):
        kind, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            corner = previous[i - channels] if i >= channels else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + up) & 255
            elif kind == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        previous = line
        rows.append(line)
    palette = chunks.get(b"PLTE", b"")
    alpha = chunks.get(b"tRNS", b"")
    rgba = []
    for line in rows:
        out = bytearray()
        for x in range(width):
            if colour == 6:
                out += line[x * 4:x * 4 + 4]
            elif colour == 2:
                out += line[x * 3:x * 3 + 3] + b"\xff"
            elif colour == 0:
                out += bytes((line[x],) * 3) + b"\xff"
            elif colour == 4:
                out += bytes((line[x * 2],) * 3) + bytes((line[x * 2 + 1],))
            else:
                index = line[x]
                out += palette[index * 3:index * 3 + 3] + bytes((alpha[index] if index < len(alpha) else 255,))
        rgba.append(out)
    return width, height, rgba


def reduce(rows: list[bytearray], width: int, height: int, out_w: int, out_h: int) -> list[bytearray]:
    """Area average, colours weighted by alpha (no dark fringes)."""
    result = []
    for y in range(out_h):
        top, bottom = y * height // out_h, max((y + 1) * height // out_h, y * height // out_h + 1)
        line = bytearray()
        for x in range(out_w):
            left, right = x * width // out_w, max((x + 1) * width // out_w, x * width // out_w + 1)
            r = g = b = a = count = 0
            for yy in range(top, bottom):
                row = rows[yy]
                for xx in range(left, right):
                    pr, pg, pb, pa = row[xx * 4:xx * 4 + 4]
                    r += pr * pa
                    g += pg * pa
                    b += pb * pa
                    a += pa
                    count += 1
            line += bytes((r // a, g // a, b // a, a // count)) if a else b"\0\0\0\0"
        result.append(line)
    return result


def encode(rows: list[bytearray], fmt: str) -> bytes:
    out = bytearray()
    for row in rows:
        if fmt == "8888":
            # 32-bit DDS: B, G, R, A in memory.
            for x in range(0, len(row), 4):
                r, g, b, a = row[x:x + 4]
                out += bytes((b, g, r, a))
            continue
        for x in range(0, len(row), 4):
            r, g, b, a = row[x:x + 4]
            if fmt == "1555":
                word = (0x8000 if a >= 128 else 0) | ((r * 31 + 127) // 255) << 10 | ((g * 31 + 127) // 255) << 5 | (b * 31 + 127) // 255
            else:
                word = ((a + 8) // 17) << 12 | ((r + 8) // 17) << 8 | ((g + 8) // 17) << 4 | (b + 8) // 17
            out += struct.pack("<H", word)
    return bytes(out)


# map012 was once misdetected as ARGB4444, which turns it pink in game. Keep
# this known exception independent from regenerated catalogues.
FORMAT_OVERRIDES = {
    ("ED6_DT24/map012", "h_map012._ch"): "1555",
    ("ED6_DT24/map012", "c_map012._ch"): "1555",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export", required=True, type=Path)
    parser.add_argument("--catalogue", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    with gzip.open(arguments.export, "rt", encoding="utf-8") as stream:
        images = json.load(stream).get("game_images") or {}
    catalogue = json.loads(arguments.catalogue.read_text(encoding="utf-8"))
    problems, written = [], 0
    for key, encoded in sorted(images.items()):
        item = catalogue.get(key)
        if item is None:
            problems.append(f"{key} : image inconnue")
            continue
        try:
            width, height, rows = read_png(base64.b64decode(encoded))
        except (ImageError, zlib.error, KeyError, struct.error) as error:
            problems.append(f"{key} : {error}")
            continue
        largest = item["variants"][0]
        if (width, height) != (largest["width"], largest["height"]):
            problems.append(f"{key} : {width}×{height} au lieu de {largest['width']}×{largest['height']}")
            continue
        for variant in item["variants"]:
            size = (variant["width"], variant["height"])
            pixels = rows if size == (width, height) else reduce(rows, width, height, *size)
            pixel_format = FORMAT_OVERRIDES.get((key, variant["name"]), variant["format"])
            data = encode(pixels, pixel_format)
            if "dds_header" in variant:
                # DDS (.ds): the original header, then the pixels in its format.
                data = base64.b64decode(variant["dds_header"]) + data
            if len(data) != variant["size"]:
                problems.append(f"{key} : {variant['name']} ferait {len(data)} octets au lieu de {variant['size']}")
                continue
            target = arguments.output / item["archive"] / variant["name"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written += 1
    if problems:
        print("ERREUR: Images refusées :\n- " + "\n- ".join(problems), file=sys.stderr)
        return 1
    print(json.dumps({"images": len(images), "files": written}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
