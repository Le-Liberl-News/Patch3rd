"""Apply the platform's shared glyph advances to already French-patched Sky executables.
Only the proven contiguous printable-character table is replaced. Control bytes stay game-specific.
"""
import struct
BASE = [10, 6, 7, 11, 10, 15, 13, 4, 6, 6, 8, 10, 5, 7, 4, 8, 9, 8, 10, 10, 10, 10, 10, 10, 10, 10, 5, 5, 10, 11, 10, 9, 15, 13, 11, 13, 13, 10, 10, 13, 13, 5, 9, 11, 10, 14, 12, 12, 10, 13, 12, 11, 10, 12, 12, 13, 11, 11, 11, 6, 8, 6, 10, 10, 6, 9, 10, 9, 10, 8, 8, 10, 10, 5, 6, 9, 5, 15, 10, 9, 10, 10, 7, 8, 7, 9, 10, 13, 9, 9, 9, 7, 4, 7, 11, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 8, 12, 12, 8, 8, 12, 8, 13, 10, 10, 10, 10, 7, 7, 12, 12, 12, 12, 9, 9, 9, 9, 8, 8, 8, 8, 6, 6, 9, 10, 10, 10, 9, 13, 15, 11, 12, 7, 9, 9, 14, 5, 9, 10, 1, 9]

def apply(executable: bytes, font: dict | None) -> bytes:
    if font is None:
        return executable
    widths = font.get('widths')
    if not isinstance(widths, list) or len(widths) != 205 or any(type(w) is not int or not 0 <= w < 64 for w in widths):
        raise ValueError('Table de chasse Trails invalide')
    old = struct.pack('<173I', *BASE)
    new = struct.pack('<173I', *widths[32:])
    if executable.count(old) != 1:
        raise ValueError('Table de chasse française absente ou ambiguë : compilation refusée')
    return executable.replace(old, new, 1)
