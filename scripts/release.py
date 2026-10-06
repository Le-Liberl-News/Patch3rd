"""Turns the build's patch (full archives and executables) into a release that
holds only differences, so no original game data is published: one xdelta per
changed file, English original -> French, and patch.json (file, SHA-256 of the
English original expected, SHA-256 of the French result).

The installer (or appliquer.bat) keeps the player's English files in dat_en
and always applies the deltas to them: any version installs over any other.

    python scripts/release.py --patch job/Sky3rd-test-patch.zip --toolchain build/toolchain \
        --xdelta xdelta3 --out release --tag 2026.10.04-1502

Writes release/Patch3rd-<tag>.zip.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def original(toolchain: Path, name: str, work: Path) -> Path | None:
    """The English file the game ships under `name` (archive parts joined)."""
    if name.lower().endswith(".exe"):
        path = toolchain / "base_exe" / name
        return path if path.is_file() else None
    base = toolchain / "base_archives"
    if (base / name).is_file():
        return base / name
    parts = sorted(base.glob(f"{name}.part*"))
    if not parts:
        return None
    joined = work / f"original_{name}"
    with joined.open("wb") as target:
        for part in parts:
            with part.open("rb") as source:
                shutil.copyfileobj(source, target, 1 << 20)
    return joined


def window(source: Path) -> int:
    """Source window covering the whole original: blocks moved anywhere in an
    archive are found again (the applier uses the same rule)."""
    return max(64 << 20, (source.stat().st_size + (1 << 20)) // (1 << 20) * (1 << 20))


def videos(xdelta: str, bundle: Path, work: Path) -> list[dict]:
    """French videos committed in videos/ (game file name, or .partNN pieces).

    A re-encoded video shares nothing with the English one: its delta is built
    without the original (not in the repository), and decodes the same when
    the installer passes the English file as source.
    """
    folder = ROOT / "videos"
    originals = json.loads((folder / "originaux.json").read_text(encoding="utf-8"))
    entries = []
    for name, original_sha in originals.items():
        parts = sorted(folder.glob(f"{name}.part*"))
        sources = [folder / name] if (folder / name).is_file() else parts
        if not sources:
            continue
        video = work / name
        with video.open("wb") as target:
            for part in sources:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, target, 1 << 20)
        delta = bundle / "deltas" / f"{name}.xdelta"
        subprocess.run([xdelta, "-e", "-1", "-S", "djw", "-f", str(video), str(delta)], check=True)
        check = work / f"check_{name}"
        subprocess.run([xdelta, "-d", "-f", str(delta), str(check)], check=True)
        if digest(check) != digest(video):
            raise SystemExit(f"Delta de la vidéo {name} : relecture différente")
        entries.append({"file": name, "original_sha256": original_sha, "patched_sha256": digest(video),
                        "delta": f"deltas/{name}.xdelta", "size": video.stat().st_size})
        print(f"{name} : vidéo française ({video.stat().st_size / 1e6:.1f} Mo)")
        check.unlink()
        video.unlink()
    return entries


def base_version(toolchain: Path) -> dict:
    """The English version the patch is built on: the build date of its
    executable (PE header), which the installer compares with the player's."""
    path = toolchain / "base_exe" / "ed6_win3.exe"
    if not path.is_file():
        return {}
    data = path.read_bytes()
    header = struct.unpack_from("<I", data, 0x3C)[0]
    built = datetime.datetime.fromtimestamp(struct.unpack_from("<I", data, header + 8)[0], datetime.timezone.utc)
    return {"exe": "ed6_win3.exe", "built": built.strftime("%Y-%m-%d"), "timestamp": int(built.timestamp())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--toolchain", required=True, type=Path)
    parser.add_argument("--xdelta", default="xdelta3")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tag", required=True)
    arguments = parser.parse_args()

    arguments.out.mkdir(parents=True, exist_ok=True)
    bundle = arguments.out / f"Patch3rd-{arguments.tag}"
    shutil.rmtree(bundle, ignore_errors=True)
    (bundle / "deltas").mkdir(parents=True)
    files = []
    with tempfile.TemporaryDirectory() as temporary, zipfile.ZipFile(arguments.patch) as patch:
        work = Path(temporary)
        for member in patch.infolist():
            name = member.filename
            if name == "build.log":
                patch.extract(member, bundle)
                continue
            source = original(arguments.toolchain, name, work)
            if source is None:
                raise SystemExit(f"Original anglais introuvable pour {name}")
            target = work / name
            with patch.open(member) as stream, target.open("wb") as out:
                shutil.copyfileobj(stream, out, 1 << 20)
            source_sha, target_sha = digest(source), digest(target)
            if source_sha != target_sha:
                delta = bundle / "deltas" / f"{name}.xdelta"
                size = str(window(source))
                subprocess.run([arguments.xdelta, "-e", "-1", "-S", "djw", "-B", size, "-f",
                                "-s", str(source), str(target), str(delta)], check=True)
                # Level 1: as small as -9 here (the archives keep their untouched
                # blocks) and a hundred times faster (DT35: 11 s instead of 16 min).
                # Read back: the delta applied to the original gives the French file.
                check = work / f"check_{name}"
                subprocess.run([arguments.xdelta, "-d", "-f", "-B", size, "-s", str(source), str(delta), str(check)],
                               check=True)
                if digest(check) != target_sha:
                    raise SystemExit(f"Delta de {name} : relecture différente")
                check.unlink()
                files.append({"file": name, "original_sha256": source_sha, "patched_sha256": target_sha,
                              "delta": f"deltas/{name}.xdelta", "size": target.stat().st_size})
                print(f"{name} : delta de {delta.stat().st_size / 1e6:.1f} Mo")
            target.unlink()
            if source.parent == work:
                source.unlink()

        files.extend(videos(arguments.xdelta, bundle, work))

    (bundle / "patch.json").write_text(json.dumps({"game": "sky-3rd", "version": arguments.tag,
                                                   "base": base_version(arguments.toolchain), "files": files},
                                                  indent=1), encoding="utf-8")
    for item in (ROOT / "patch").iterdir():
        shutil.copyfile(item, bundle / item.name)
    archive = arguments.out / f"Patch3rd-{arguments.tag}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(bundle).as_posix())
    shutil.rmtree(bundle)
    print(f"Release : {archive} ({archive.stat().st_size / 1e6:.1f} Mo, {len(files)} fichiers modifiés)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
