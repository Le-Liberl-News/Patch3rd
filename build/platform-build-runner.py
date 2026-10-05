from __future__ import annotations

import argparse
import difflib
import gzip
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ed6_splice import SpliceError, splice  # noqa: E402


SET_TITLE_QUOTED = re.compile(
    r'^(?P<prefix>\s*\w*SetTitle\b[^\"]*")(?P<text>(?:\\.|[^"\\])*)(?P<suffix>".*)$'
)
SET_TITLE_BLOCK = re.compile(r'^(?P<indent>\s*)\w*SetTitle\b.*\{\s*$')
CHIP_PATHS = re.compile(
    r'^(?P<prefix>\s*chip\[\d+\]\s+)"(?P<first>[^"]+)"\s+"(?P<second>[^"]+)"(?P<suffix>.*)$'
)
FUNCTION_LINE = re.compile(r"^\s*fn\[(\d+)\]:\s*$", re.MULTILINE)
TEXTUAL_BLOCK_START = re.compile(
    r"^(?P<indent>\s*)(?:TextTalkNamed|TextTalk|TextMessage|Menu|\w*SetTitle)\b.*(?:\{|:)\s*$",
    re.IGNORECASE,
)


VOICE_TAG = re.compile(r"#[0-9]+v")


def carry_voices(structure: str, voiced: str) -> tuple[str, list[str]]:
    """Put the voices of the toolchain CLM into a structure edited on the platform.

    The platform keeps scripts without voice tags. A line identical to a line
    of the voiced CLM (voices and pauses aside: SoraVoice adds a few pauses to
    start a voice with its page) takes the voiced line; a line moved elsewhere
    finds its voice when its text is unique in the script. Returns the voices
    that could not be carried over (lines modified in the structure).
    """
    def key(line: str) -> str:
        return VOICE_TAG.sub("", line).replace("{wait}", "").rstrip("\r\n")

    if VOICE_TAG.sub("", voiced) == structure:
        return voiced, []
    target = structure.splitlines(keepends=True)
    source = voiced.splitlines(keepends=True)
    target_keys = [key(line) for line in target]
    source_keys = [key(line) for line in source]
    carried = [False] * len(source)
    assigned = [False] * len(target)
    result = list(target)
    matcher = difflib.SequenceMatcher(a=source_keys, b=target_keys, autojunk=False)
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            result[block.b + offset] = source[block.a + offset]
            carried[block.a + offset] = assigned[block.b + offset] = True
    unique: dict[str, int] = {}
    for index, line_key in enumerate(source_keys):
        unique[line_key] = -1 if line_key in unique else index
    for index, line in enumerate(target):
        if assigned[index] or VOICE_TAG.search(line):
            continue
        origin = unique.get(target_keys[index], -1)
        if origin >= 0 and not carried[origin] and VOICE_TAG.search(source[origin]):
            result[index] = source[origin]
            carried[origin] = True
    lost = [
        tag for index, line in enumerate(source) if not carried[index]
        for tag in VOICE_TAG.findall(line)
    ]
    return "".join(result), lost


def write_status(job: Path, stage: str, progress: int, message: str, *, error: bool = False) -> None:
    temporary = job / "status.tmp"
    temporary.write_text(json.dumps({
        "stage": stage,
        "progress": max(0, min(100, progress)),
        "message": message,
        "complete": stage == "complete",
        "error": error,
    }, ensure_ascii=False), encoding="utf-8")
    temporary.replace(job / "status.json")


def normalized(core, value: str) -> str:
    return core.normalized(value or "")


def scan_slots(core, lines: list[str]):
    slots = core.scan_slots(lines)
    occupied = {(slot.start, slot.end) for slot in slots}
    for index, line in enumerate(lines):
        bare = line.rstrip("\r\n")
        quoted = SET_TITLE_QUOTED.match(bare)
        if quoted and (index, index + 1) not in occupied:
            slots.append(core.Slot(
                "quoted", index, index + 1, core.unescape_quoted(quoted.group("text")),
                prefix=quoted.group("prefix"), suffix=quoted.group("suffix"),
            ))
            continue
        block = SET_TITLE_BLOCK.match(bare)
        if not block:
            continue
        block_indent = block.group("indent")
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].rstrip("\r\n") != block_indent + "}":
            cursor += 1
        if cursor >= len(lines):
            raise ValueError(f"SetTitle non fermé à la ligne {index + 1}")
        start = index + 1
        if (start, cursor) in occupied:
            continue
        content = lines[start:cursor]
        if not content:
            continue
        content_indent = re.match(r"^\s*", content[0]).group(0)
        raw = "\n".join(value.rstrip("\r\n")[len(content_indent):] for value in content)
        english = core.clean_dialogue(raw)
        if english:
            slots.append(core.Slot(
                "text", start, cursor, english, content_indent,
                core.LEADING_TAGS.match(raw).group(0),
                "{wait}" if raw.endswith("{wait}") else "", raw,
            ))
    slots.sort(key=lambda slot: (slot.start, slot.end))
    return slots


def instruction_units(lines: list[str], slots: list) -> list[dict]:
    """Locate the complete Calmare instruction owning each visible text slot."""
    owners: list[tuple[int, int]] = []
    for slot in slots:
        start = int(slot.start)
        if slot.kind == "text":
            cursor = start - 1
            while cursor >= 0 and not TEXTUAL_BLOCK_START.match(lines[cursor].rstrip("\r\n")):
                if FUNCTION_LINE.match(lines[cursor].rstrip("\r\n")):
                    break
                cursor -= 1
            if cursor < 0 or not TEXTUAL_BLOCK_START.match(lines[cursor].rstrip("\r\n")):
                raise RuntimeError(f"Instruction parente introuvable à la ligne {start + 1}")
            instruction_start = cursor
            match = TEXTUAL_BLOCK_START.match(lines[cursor].rstrip("\r\n"))
            indent = match.group("indent")
            cursor += 1
            while cursor < len(lines):
                marker = lines[cursor].rstrip("\r\n")
                if marker == indent + "}":
                    cursor += 1
                    break
                cursor += 1
            if cursor > len(lines):
                raise RuntimeError(f"Instruction non fermée à la ligne {instruction_start + 1}")
            owners.append((instruction_start, cursor))
        else:
            line = lines[start].rstrip("\r\n")
            if re.match(r'^\s*"', line):
                cursor = start - 1
                while cursor >= 0:
                    match = TEXTUAL_BLOCK_START.match(lines[cursor].rstrip("\r\n"))
                    if match and re.search(r"\bMenu\b", lines[cursor], re.IGNORECASE):
                        break
                    if FUNCTION_LINE.match(lines[cursor].rstrip("\r\n")):
                        break
                    cursor -= 1
                instruction_start = cursor if cursor >= 0 else start
                base_indent = len(re.match(r"^\s*", lines[instruction_start]).group(0))
                cursor = instruction_start + 1
                while cursor < len(lines):
                    candidate = lines[cursor].rstrip("\r\n")
                    indent = len(re.match(r"^\s*", candidate).group(0))
                    if candidate.strip() and indent <= base_indent:
                        break
                    cursor += 1
                owners.append((instruction_start, cursor))
            else:
                owners.append((start, start + 1))

    counts: dict[tuple[int, int], int] = {}
    ordinals: dict[tuple[int, int], int] = {}
    for owner in owners:
        counts[owner] = counts.get(owner, 0) + 1
    result = []
    for slot, owner in zip(slots, owners):
        ordinals[owner] = ordinals.get(owner, 0) + 1
        result.append({
            "instruction_start_line": owner[0] + 1,
            "instruction_end_line": owner[1],
            "instruction_page_ordinal": ordinals[owner],
            "instruction_page_count": counts[owner],
            "instruction_raw": "".join(lines[owner[0]:owner[1]]),
            "instruction_page_raw": "".join(lines[int(slot.start):int(slot.end)]),
        })
    return result


def load_asset_ids(index_directory: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for index_path in sorted(index_directory.glob("ED6_DT*.json")):
        document = json.loads(index_path.read_text(encoding="utf-8"))
        for identifier, entry in document.items():
            if not isinstance(entry, dict):
                continue
            path = entry.get("path") or entry.get("name")
            if path:
                normalized_path = str(path).replace("\\", "/").casefold()
                result[normalized_path] = str(identifier)
                result.setdefault(Path(normalized_path).name, str(identifier))
    return result


def restore_raw_chip_references(lines: list[str], asset_ids: dict[str, str]) -> int:
    changed = 0
    for index, line in enumerate(lines):
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        match = CHIP_PATHS.match(line.rstrip("\r\n"))
        if not match:
            continue
        first = asset_ids.get(match.group("first").replace("\\", "/").casefold())
        second = asset_ids.get(match.group("second").replace("\\", "/").casefold())
        if first is None or second is None:
            continue
        lines[index] = (
            match.group("prefix") + f"file[{first}] file[{second}]"
            + match.group("suffix") + newline
        )
        changed += 1
    return changed


def replace_slot(core, lines: list[str], slot, displayed_english: str, translation: str,
                 dropped: list[str]) -> None:
    """Inject a translation, keeping leading spaces that Calmare would read as indentation.

    Calmare's lexer drops spaces/tabs at the start of a text line; its
    decompiler protects them with an empty ``{}`` (e.g. aligned help screens).
    """
    if slot.kind != "text":
        core.replace_slot(lines, slot, displayed_english, translation, dropped)
        return
    newline = "\r\n" if lines[slot.start].endswith("\r\n") else "\n"
    translated = core.restore_hidden_controls(
        slot.raw, displayed_english, core.encode_french_glyphs(translation), dropped
    )
    lines[slot.start:slot.end] = [
        slot.indent + ("{}" + value if value[:1] in (" ", "\t") else value) + newline
        for value in translated.split("\n")
    ]


def unencodable(text: str) -> list[str]:
    """Characters left that the game font encoding (CP932) cannot represent.

    Checked after the French glyph table, so that one build lists every
    offending bubble instead of Calmare stopping at the first one.
    """
    missing = []
    for character in dict.fromkeys(text):
        if character in "\r\n":
            continue
        try:
            character.encode("cp932")
        except UnicodeEncodeError:
            name = unicodedata.name(character, "?")
            missing.append(f"U+{ord(character):04X} {character!r} ({name})")
    return missing


def inject_script(
    core, script: dict, characters: list[dict], source_text: str, output: Path,
    asset_ids: dict[str, str],
) -> dict:
    lines = source_text.splitlines(keepends=True)
    slots = scan_slots(core, lines)
    entries = script["entries"]
    if len(slots) != len(entries):
        raise ValueError(f"{script['code']}: {len(entries)} entrées plateforme mais {len(slots)} emplacements CLM")
    changed = 0
    failures: list[str] = []
    dropped_controls: list[str] = []
    for entry, slot in reversed(list(zip(entries, slots))):
        translation = str(entry.get("translation_fr", ""))
        english = str(entry.get("source_en", ""))
        # The game shows exactly the platform's text: an empty translation is an
        # empty bubble. Only a translation equal to the English keeps the CLM
        # operand as it is (same text, with its page breaks and hidden controls).
        if normalized(core, translation) == normalized(core, english):
            continue
        desired = translation
        missing = unencodable(core.encode_french_glyphs(desired))
        if missing:
            failures.append(
                f"{script['code']} bulle {entry.get('ordinal', '?')} "
                f"(ligne source {entry.get('source_line', '?')}) : caractère(s) sans équivalent "
                f"dans l'encodage du jeu : {', '.join(missing)}"
            )
            continue
        if normalized(core, desired) != normalized(core, slot.english):
            try:
                # Internal pauses the translator left out cannot be placed
                # without guessing (PatchSC adds none either): they are
                # omitted and left to review.
                dropped: list[str] = []
                replace_slot(core, lines, slot, slot.english, desired, dropped)
                if dropped:
                    dropped_controls.append(
                        f"{script['code']} bulle {entry.get('ordinal', '?')} "
                        f"(ligne source {entry.get('source_line', '?')}) : "
                        + "; ".join(dropped)
                    )
                changed += 1
            except ValueError as error:
                source_pages = sum(
                    1 for match in core._control_occurrences(slot.raw)
                    if match.group(0) == "{wait}"
                )
                translated_text = desired.replace("\r\n", "\n")
                translated_lines = len(translated_text.split("\n"))
                translated_paragraphs = len(re.split(r"\n\s*\n", translated_text))
                failures.append(
                    f"{script['code']} bulle {entry.get('ordinal', '?')} "
                    f"(ligne source {entry.get('source_line', '?')}, "
                    f"{source_pages} pages source, {translated_lines} lignes FR, "
                    f"{translated_paragraphs} paragraphes FR): {error}"
                )

    if failures:
        raise ValueError("\n".join(failures))
    for message in reversed(dropped_controls):
        # A voice follows a page of the English the French does not have.
        kind = "voix sans page FR correspondante, omises" if VOICE_TAG.search(message) else "codes absents du FR, omis"
        print(f"AVERTISSEMENT {kind} : {message}")

    names_changed = 0
    if any(character.get("kind") for character in characters):
        # PatchSC's names: one per npc slot in order, and punctual names.
        names = core.WorkbookNames(
            tuple(
                core.NameRow(int(character["ordinal"]), str(character.get("source_en") or ""),
                             str(character.get("translation_fr") or ""))
                for character in sorted(characters, key=lambda item: int(item["ordinal"]))
                if character.get("kind") == "npc"
            ),
            {
                str(character["source_en"]): str(character.get("translation_fr") or "")
                for character in characters
                if character.get("kind") == "punctual" and character.get("source_en")
            },
        )
        for character in characters:
            missing = unencodable(core.encode_french_glyphs(str(character.get("translation_fr") or "")))
            if missing:
                raise ValueError(
                    f"{script['code']} nom {character.get('source_en')!r} : caractère(s) sans équivalent "
                    f"dans l'encodage du jeu : {', '.join(missing)}"
                )
        names_changed = core.apply_workbook_names(lines, names, script["code"])
        characters = []
    translated_names = {
        str(character.get("source_en", "")): str(character.get("translation_fr", ""))
        for character in characters
        if character.get("source_en") and character.get("translation_fr")
    }
    if translated_names:
        for index, line in enumerate(lines):
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            bare = line.rstrip("\r\n")
            match = core.CHARACTER_NAME.match(bare) or core.SET_NAME.match(bare) or core.NAMED_TALK.match(bare)
            if not match:
                continue
            original = core.unescape_quoted(match.group("name"))
            replacement = translated_names.get(original)
            if replacement and replacement != original:
                replacement = core.encode_french_glyphs(replacement)
                missing = unencodable(replacement)
                if missing:
                    raise ValueError(
                        f"{script['code']} nom {original!r} : caractère(s) sans équivalent "
                        f"dans l'encodage du jeu : {', '.join(missing)}"
                    )
                lines[index] = (
                    match.group("prefix") + core.escape_quoted(replacement)
                    + match.group("suffix") + newline
                )
                names_changed += 1

    restored_assets = restore_raw_chip_references(lines, asset_ids)
    with output.open("w", encoding="utf-8", newline="") as stream:
        stream.write("".join(lines))
    return {
        "slots": len(slots), "changed": changed, "names_changed": names_changed,
        "restored_assets": restored_assets,
    }


def command(arguments: list[str], cwd: Path, timeout: int = 120) -> str:
    process = subprocess.run(
        arguments, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    output = (process.stdout + process.stderr).strip()
    if process.returncode != 0:
        raise RuntimeError(f"Commande échouée ({process.returncode}) : {' '.join(arguments)}\n{output}")
    return output


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def roundtrip_text(core, value: str) -> str:
    # {} only protects leading spaces and writes no byte: Calmare drops it when
    # a voice tag now opens the line.
    return normalized(core, value).replace("{item item[", "{item[").replace("{}", "")


def validate_clm_roundtrip(
    core, expected: Path, redecompiled: Path, expected_binary: Path,
    roundtrip_binary: Path, code: str,
) -> None:
    expected_text = expected.read_text(encoding="utf-8")
    actual_text = redecompiled.read_text(encoding="utf-8")
    if digest(expected_binary) != digest(roundtrip_binary):
        raise RuntimeError(f"{code}: le binaire change après décompilation/recompilation Calmare")
    expected_functions = FUNCTION_LINE.findall(expected_text)
    actual_functions = FUNCTION_LINE.findall(actual_text)
    if expected_functions != actual_functions:
        raise RuntimeError(f"{code}: ordre des fonctions modifié par le roundtrip Calmare")
    expected_slots = scan_slots(core, expected_text.splitlines(keepends=True))
    actual_slots = scan_slots(core, actual_text.splitlines(keepends=True))
    if len(expected_slots) != len(actual_slots):
        raise RuntimeError(
            f"{code}: {len(expected_slots)} textes avant compilation contre {len(actual_slots)} après roundtrip"
        )
    for ordinal, (expected_slot, actual_slot) in enumerate(zip(expected_slots, actual_slots), 1):
        # Calmare 0.1.4 decompiles {item item[N]} as {item[N]}: same bytes, other spelling.
        if roundtrip_text(core, expected_slot.english) != roundtrip_text(core, actual_slot.english):
            raise RuntimeError(f"{code} #{ordinal}: texte modifié par le roundtrip Calmare")


def locate_extracted(root: Path, name: str) -> Path:
    matches = list(root.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"Vérification : {name} trouvé {len(matches)} fois après extraction")
    return matches[0]


def rebuild_archive(archive_tool: str, toolchain: Path, job: Path, name: str, files: list[Path],
                    patch: zipfile.ZipFile | None = None) -> None:
    """Writes NAME.dir/.dat, in the job folder or straight into the patch zip
    (the archive then never takes room on the disk): The 3rd's English archive
    with `files` in place of the English entries of the same name, copied block
    by block from the base (tools/ed6_splice.py), no English block left behind,
    no extraction. Fonts (._da) are stored uncompressed, as in Sky SC's patch.
    Each replaced block is read back."""
    base = toolchain / "base_archives"
    parts = sorted(base.glob(f"{name}.dat.part*")) or [base / f"{name}.dat"]
    if not (base / f"{name}.dir").is_file() or not all(part.is_file() for part in parts):
        raise RuntimeError(f"Archive anglaise de base absente : {name}")
    for extension in ("dir", "dat"):
        (job / f"{name}.{extension}").unlink(missing_ok=True)
    try:
        splice(archive_tool, base / f"{name}.dir", parts, files, patch or job, job / f"carrier_{name}",
               lambda arguments: command(arguments, job, 600))
    except SpliceError as error:
        raise RuntimeError(str(error)) from error
    finally:
        shutil.rmtree(job / f"carrier_{name}", ignore_errors=True)


# What a finished job keeps: the patch, its status and its logs.
KEPT_IN_JOB = {"Sky3rd-test-patch.zip", "status.json", "metadata.json", "build.log", "launcher.log", "pid", "downloaded"}


def check_free_space(job: Path, toolchain: Path) -> None:
    """Stops before starting when the disk could fill up: a full disk takes the
    whole site down (sessions, database). The large archives go straight into
    the patch zip: the job holds the zip (about 3/4 of the archives once
    compressed) and a few hundred megabytes of scripts and pictures."""
    archives = sum(path.stat().st_size for path in (toolchain / "base_archives").iterdir() if path.is_file())
    needed = archives * 3 // 4 + (400 << 20)
    free = shutil.disk_usage(job).free
    if free < needed:
        raise RuntimeError(
            f"Place disque insuffisante : {free / (1 << 30):.1f} Go libres, {needed / (1 << 30):.1f} Go nécessaires. "
            "Supprimez d'anciennes compilations avant de relancer."
        )


def clean_job(job: Path) -> None:
    """Removes everything but the patch, the status and the logs."""
    for path in job.iterdir():
        if path.name in KEPT_IN_JOB:
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def tool_binary(toolchain: Path, name: str) -> str:
    windows = toolchain / "bin" / f"{name}.exe"
    native = toolchain / "bin" / name
    candidate = windows if os.name == "nt" and windows.is_file() else native
    if not candidate.is_file():
        raise RuntimeError(f"Outil absent de la toolchain : {candidate.name}")
    return str(candidate)


def python_binary() -> str:
    if sys.executable and Path(sys.executable).is_file():
        return sys.executable
    candidate = shutil.which("python3") or shutil.which("python")
    if not candidate:
        raise RuntimeError("Interpréteur Python introuvable pour compiler le lexique")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True, type=Path)
    parser.add_argument("--toolchain", required=True, type=Path)
    # Shared hosting has a tight per-account memory ceiling. Calmare is cheap
    # enough sequentially, but several compiler processes at once are killed
    # by the host before they can report a Rust error.
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    job = args.job.resolve()
    toolchain = args.toolchain.resolve()
    log_path = job / "build.log"

    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        original_stdout, original_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = log
        try:
            check_free_space(job, toolchain)
            sys.path.insert(0, str(toolchain))
            import inject_structural as core
            if "dropped" not in inspect.signature(core.restore_hidden_controls).parameters:
                raise RuntimeError(
                    "inject_structural.py de la toolchain est obsolète : "
                    "déployer la version qui accepte les pauses absentes du FR"
                )

            write_status(job, "inject", 2, "Lecture de l’export figé")
            with gzip.open(job / "compilation-export.json.gz", "rt", encoding="utf-8") as stream:
                payload = json.load(stream)
            if payload.get("kind") != "compilation" or payload.get("schema") != 1:
                raise RuntimeError("Format d’export de compilation invalide")
            # The hosting kills the account's processes above a small memory
            # ceiling: each tool gets only the part of the export it reads
            # (tables and books, or the pictures), not the whole of it on top
            # of what this process already holds.
            tables_export = job / "tables-export.json.gz"
            with gzip.open(tables_export, "wt", encoding="utf-8", compresslevel=5) as stream:
                json.dump({"lexicon_sections": payload.get("lexicon_sections", []), "books": payload.get("books", [])},
                          stream, ensure_ascii=False)
            images_export = job / "images-export.json.gz"
            with gzip.open(images_export, "wt", encoding="utf-8", compresslevel=1) as stream:
                json.dump({"game_images": payload.pop("game_images", {})}, stream)

            scripts = payload.get("scripts", [])
            entries_by_script: dict[str, list[dict]] = {str(row["code"]).lower(): [] for row in scripts}
            for entry in payload.get("entries", []):
                entries_by_script.setdefault(str(entry["script"]).lower(), []).append(entry)
            characters_by_script: dict[str, list[dict]] = {}
            for character in payload.get("characters", []):
                characters_by_script.setdefault(str(character["script"]).lower(), []).append(character)
            scenes_by_script: dict[str, list[dict]] = {}
            for scene in payload.get("scenes", []):
                scenes_by_script.setdefault(str(scene["script"]).lower(), []).append(scene)
            for rows in scenes_by_script.values():
                rows.sort(key=lambda row: int(row["ordinal"]))
            del payload
            asset_ids = load_asset_ids(toolchain / "archive_indexes")
            if not asset_ids:
                raise RuntimeError("Index des ressources de jeu absent de la toolchain")
            archive_tool = tool_binary(toolchain, "ed6-archive")

            bases = {path.stem.strip().lower(): path for path in (toolchain / "base_clm").glob("*.clm")}
            translated = job / "translated_clm"
            compiled = job / "ED6_DT21"
            redecompiled = job / "redecompiled_clm"
            translated.mkdir()
            compiled.mkdir()
            redecompiled.mkdir()
            recompiled = job / "roundtrip_binary"
            recompiled.mkdir()
            total_slots = total_changed = 0
            injection_failures: list[str] = []
            for position, script_row in enumerate(scripts, 1):
                code = str(script_row["code"]).lower()
                base = bases.get(code)
                if base is None:
                    raise RuntimeError(f"CLM source absent : {code}")
                script = {"code": code, "entries": entries_by_script.get(code, [])}
                structural_scenes = scenes_by_script.get(code, [])
                structure_complete = int(script_row.get("structure_complete") or 0) == 1
                structure_changed = int(script_row.get("structure_revision") or 1) > 1
                if structure_changed and structure_complete:
                    source_text = (
                        str(script_row.get("raw_preamble") or "")
                        + "".join(str(scene["raw_source"]) for scene in structural_scenes)
                    )
                    # The platform structure carries the voices since
                    # `platform-maintenance.php voices`; before that, they are
                    # taken from the toolchain CLM.
                    lost_voices = []
                    if not VOICE_TAG.search(source_text):
                        source_text, lost_voices = carry_voices(source_text, base.read_text(encoding="utf-8"))
                    if lost_voices:
                        print(
                            f"AVERTISSEMENT voix non reprises ({code}, lignes modifiées dans la structure) : "
                            + " ".join(lost_voices)
                        )
                else:
                    # Structural data is imported one complete script at a time. During a
                    # repair, some scripts legitimately have no scenes yet; concatenating
                    # their partial rows would silently compile a truncated CLM.
                    source_text = base.read_text(encoding="utf-8")
                    if structure_changed:
                        print(f"[{code}] structure CLM modifiée mais incomplète : source toolchain utilisée")
                # Sky 3rd scripts decompiled as FC/SC silently rewrite party member
                # ids when recompiled: never build from another dialect.
                if not source_text.startswith("calmare tc scena"):
                    raise RuntimeError(
                        f"{code} : structure CLM en dialecte {source_text.splitlines()[0]!r} ; "
                        "la structure doit être redécompilée avec « calmare -d -g tc » avant de compiler"
                    )
                try:
                    result = inject_script(
                        core, script, characters_by_script.get(code, []),
                        source_text, translated / base.name, asset_ids,
                    )
                except ValueError as error:
                    injection_failures.append(str(error))
                    continue
                total_slots += result["slots"]
                total_changed += result["changed"]
                if position % 20 == 0 or position == len(scripts):
                    progress = 2 + int(position * 36 / max(1, len(scripts)))
                    write_status(job, "inject", progress, f"Injection : {position}/{len(scripts)} fichiers")
            if injection_failures:
                raise RuntimeError(
                    "Injection refusée pour les répliques suivantes :\n- "
                    + "\n- ".join(injection_failures)
                )
            print(f"Injection validée : {len(scripts)} fichiers, {total_slots} emplacements, {total_changed} traductions")

            write_status(job, "lexicon", 39, "Compilation du lexique")
            base_archive = toolchain / "base_archives"
            for archive_name in ("ED6_DT22", "ED6_DT30", "ED6_DT3C"):
                for extension in ("dir", "dat"):
                    source = base_archive / f"{archive_name}.{extension}"
                    if not source.is_file():
                        raise RuntimeError(f"Archive anglaise de base absente : {source.name}")
                    destination = job / source.name
                    print(f"Copie base lexique : {source} -> {destination}")
                    # Les métadonnées POSIX des fichiers envoyés par FTP ne sont
                    # pas pertinentes et peuvent être interdites sur l'hébergement.
                    shutil.copyfile(source, destination)
            extracted_lexicon = job / "base_lexicon"
            print("Extraction de la base ED6_DT22")
            command([
                archive_tool, "extract", "--output", str(extracted_lexicon),
                str(job / "ED6_DT22.dir"),
            ], job, 300)
            extracted_monsters = job / "base_monsters"
            print("Extraction de la base ED6_DT3C")
            command([
                archive_tool, "extract", "--output", str(extracted_monsters),
                str(job / "ED6_DT3C.dir"),
            ], job, 300)
            base_lexicon = extracted_lexicon / "ED6_DT22"
            compiled_lexicon = job / "ED6_DT22"
            if not base_lexicon.is_dir():
                raise RuntimeError("Extraction de l’archive ED6_DT22 de base incomplète")
            base_monster_note = locate_extracted(extracted_monsters, "mnsnote2._dt")
            compiled_monsters = job / "ED6_DT3C"
            compiled_monster_note = compiled_monsters / "mnsnote2._dt"
            compiled_lexicon.mkdir()
            print("Lancement du compilateur de lexique Sky 3rd")
            lexicon_report = command([
                python_binary(), str(toolchain / "platform-third-lexicon.py"),
                "--export", str(tables_export),
                "--base", str(base_lexicon),
                "--output", str(compiled_lexicon),
                "--core", str(toolchain / "lexique_dt.py"),
                "--monster-base", str(base_monster_note),
                "--monster-output", str(compiled_monster_note),
                "--monster-mapping", str(toolchain / "third-monster-mapping.json"),
            ], job, 300)
            print(f"Lexique compilé : {lexicon_report}")
            quest_report = command([
                python_binary(), str(toolchain / "third-quest-inject.py"),
                "--export", str(tables_export),
                "--base", str(base_lexicon),
                "--output", str(compiled_lexicon),
                "--core", str(toolchain / "lexique_dt.py"),
            ], job, 300)
            print(f"Portes (quêtes) : {quest_report}")
            quiz_report = command([
                python_binary(), str(toolchain / "third-quiz-inject.py"),
                "--export", str(tables_export),
                "--base", str(base_lexicon),
                "--output", str(compiled_lexicon),
                "--core", str(toolchain / "lexique_dt.py"),
            ], job, 300)
            print(f"Quiz : {quiz_report}")
            book_report = command([
                python_binary(), str(toolchain / "third-book-inject.py"),
                "--export", str(tables_export),
                "--base", str(base_lexicon),
                "--output", str(compiled_lexicon),
                "--core", str(toolchain / "lexique_dt.py"),
            ], job, 300)
            print(f"Livres : {book_report}")
            # Game images: converted to the game's 16-bit format, every variant.
            compiled_images = job / "images"
            image_report = command([
                python_binary(), str(toolchain / "third-images-inject.py"),
                "--export", str(images_export),
                "--catalogue", str(toolchain / "third-images.json"),
                "--output", str(compiled_images),
            ], job, 900)
            print(f"Images : {image_report}")
            # Put them back into their archives (ED6_DT20, ED6_DT24…), from the English
            # base archives; a .dat over GitHub's file limit is stored in parts. The
            # French fonts of Sky SC's patch go into ED6_DT20 too: The 3rd's English
            # fonts are byte for byte Sky SC's (toolchain/fonts_fr).
            archive_files: dict[str, list[Path]] = {}
            for folder in (compiled_images, toolchain / "fonts_fr"):
                for archive_dir in sorted(path for path in folder.iterdir() if path.is_dir()) if folder.is_dir() else []:
                    archive_files.setdefault(archive_dir.name, []).extend(
                        sorted(path for path in archive_dir.iterdir() if path.suffix.lower() in ("._ch", "._ds", "._da"))
                    )
            # The patch is written as the archives come: each large archive goes
            # into it as soon as it is built, then leaves the disk.
            patch = zipfile.ZipFile(job / "Sky3rd-test-patch.zip.part", "w", zipfile.ZIP_DEFLATED, compresslevel=7)
            image_archives = []
            for name, files in sorted(archive_files.items()):
                rebuild_archive(archive_tool, toolchain, job, name, files, patch)
                image_archives.append(name)
            print(f"Archives d'images et polices : {image_archives or 'aucune'}")

            # Texts of the executables (table "Exécutable"): both builds patched.
            write_status(job, "exe", 41, "Textes de l'exécutable")
            compiled_exe = job / "exe"
            exe_report = command([
                python_binary(), str(toolchain / "third-exe-inject.py"),
                "--export", str(tables_export),
                "--strings", str(toolchain / "third-exe-strings.json"),
                "--dx8", str(toolchain / "base_exe" / "ed6_win3.exe"),
                "--dx9", str(toolchain / "base_exe" / "ed6_win3_DX9.exe"),
                "--output", str(compiled_exe),
                "--core", str(toolchain / "lexique_dt.py"),
            ], job, 300)
            print(f"Exécutables : {exe_report}")

            # Battle scripts (table "Combats (AS)"): rebuilt in ED6_DT30.
            write_status(job, "as", 41, "Scripts de combat")
            extracted_battle = job / "base_battle"
            command([
                archive_tool, "extract", "--output", str(extracted_battle),
                str(job / "ED6_DT30.dir"),
            ], job, 300)
            compiled_battle = job / "ED6_DT30"
            battle_report = command([
                python_binary(), str(toolchain / "third-as-inject.py"),
                "--export", str(tables_export),
                "--base", str(extracted_battle / "ED6_DT30"),
                "--output", str(compiled_battle),
                "--core", str(toolchain / "lexique_dt.py"),
            ], job, 300)
            print(f"Scripts de combat : {battle_report}")

            write_status(job, "compile", 42, "Compilation Calmare")
            calmare = tool_binary(toolchain, "calmare")

            def compile_one(script_row: dict) -> tuple[str, str, str | None]:
                """Compiles a translated script. One that cannot be compiled (too
                long, invalid code) goes in English, from its original CLM, so
                that the rest of the patch still comes out (third value: why)."""
                code = str(script_row["code"]).lower()
                base = bases[code]
                source = translated / base.name
                target = compiled / f"{code}._sn"
                failure: RuntimeError | None = None
                try:
                    output = command([calmare, str(source), "-c", "-o", str(target)], job, 90)
                except RuntimeError as error:
                    failure = error
                    voiced = source.read_text(encoding="utf-8")
                    if "as a u16" in str(error) and VOICE_TAG.search(voiced):
                        # Too long with its voices: until the script is split,
                        # the French goes without them rather than in English.
                        silent = translated / f"{code}.sans-voix.clm"
                        silent.write_bytes(VOICE_TAG.sub("", voiced).encode("utf-8"))
                        try:
                            output = command([calmare, str(silent), "-c", "-o", str(target)], job, 90)
                            source, failure = silent, None
                            print(f"AVERTISSEMENT voix retirées : {code} : script trop long avec les voix, à découper")
                        except RuntimeError as silent_error:
                            failure = silent_error
                if failure is not None:
                    error = failure
                    overflow = re.search(r"attempted to write 0x([0-9A-Fa-f]+) as a u16", str(error))
                    if overflow is None:
                        # Calmare points at the faulty text: keep that line in the warning
                        # (its colour codes removed).
                        plain = re.sub(r"\[[0-9;]*[A-Za-z]", "", str(error))
                        pointed = re.search(r"^\s*\d+\s+[│|]\s+(.+)$", plain, re.M)
                        line = pointed.group(1).strip() if pointed else ""
                        reason = "compilation impossible" + (f" : « {line} »" if len(line) > 2 else "")
                    else:
                        # Every pointer of an ED6 script is 16-bit: a file cannot exceed 64 KiB.
                        excess = int(overflow.group(1), 16) - 0xFFFF
                        amount = f"{excess:,}".replace(",", " ")
                        reason = f"script trop long pour le format du jeu (au moins {amount} octets en trop), à découper"
                    try:
                        command([calmare, str(base), "-c", "-o", str(target)], job, 90)
                    except RuntimeError as error:
                        if "as a u16" not in str(error):
                            raise
                        # Some English scripts exceed 64 KiB once voiced: until
                        # they are split, they go without their voices.
                        silent = translated / f"{code}.sans-voix.clm"
                        silent.write_bytes(VOICE_TAG.sub("", base.read_text(encoding="utf-8")).encode("utf-8"))
                        command([calmare, str(silent), "-c", "-o", str(target)], job, 90)
                        reason += " ; anglais trop long avec les voix, compilé sans voix"
                    return code, "", reason
                roundtrip = redecompiled / base.name
                first_line = source.read_text(encoding="utf-8").splitlines()[0].strip()
                dialect = re.fullmatch(r"calmare\s+(fc|sc|tc)\s+scena", first_line)
                if dialect is None:
                    raise RuntimeError(f"{code}: dialecte Calmare inconnu : {first_line!r}")
                roundtrip_output = command(
                    [calmare, str(target), "-d", "-g", dialect.group(1), "-o", str(roundtrip)], job, 90
                )
                roundtrip_target = recompiled / f"{code}._sn"
                recompile_output = command(
                    [calmare, str(roundtrip), "-c", "-o", str(roundtrip_target)], job, 90
                )
                validate_clm_roundtrip(core, source, roundtrip, target, roundtrip_target, code)
                return code, "\n".join(
                    value for value in (output, roundtrip_output, recompile_output) if value
                ), None

            completed = 0
            compile_failures: list[str] = []
            left_in_english: list[str] = []
            with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as executor:
                futures = [executor.submit(compile_one, row) for row in scripts]
                for future in as_completed(futures):
                    completed += 1
                    # Every file is compiled so that one build reports all the problems.
                    try:
                        code, output, fallback = future.result()
                    except RuntimeError as error:
                        compile_failures.append(str(error))
                        continue
                    if fallback:
                        left_in_english.append(code)
                        print(f"AVERTISSEMENT fichier laissé en anglais : {code} : {fallback}")
                    if output:
                        print(f"[{code}] {output}")
                    if completed % 20 == 0 or completed == len(scripts):
                        progress = 42 + int(completed * 30 / max(1, len(scripts)))
                        write_status(job, "compile", progress, f"Compilation : {completed}/{len(scripts)} fichiers")

            if compile_failures:
                raise RuntimeError(
                    "Compilation refusée pour les fichiers suivants :\n- " + "\n- ".join(sorted(compile_failures))
                )
            shutil.copy2(toolchain / "ED6_DT21.json", job / "ED6_DT21.json")
            write_status(job, "archive", 74, "Création des archives ED6_DT21, ED6_DT22 et ED6_DT3C")
            command([
                archive_tool, "create", "--output", str(job),
                str(job / "ED6_DT21.json"),
            ], job, 900)
            # Rebuilt whole from the English archives: no English block left behind.
            rebuild_archive(archive_tool, toolchain, job, "ED6_DT22", sorted(compiled_lexicon.glob("*._dt")))
            rebuild_archive(archive_tool, toolchain, job, "ED6_DT3C", [compiled_monster_note])
            rebuild_archive(archive_tool, toolchain, job, "ED6_DT30", sorted(compiled_battle.glob("*._dt")))
            for archive_name in ("ED6_DT21", "ED6_DT22", "ED6_DT30", "ED6_DT3C"):
                if not (job / f"{archive_name}.dir").is_file() or not (job / f"{archive_name}.dat").is_file():
                    raise RuntimeError(f"ed6-archive n’a pas produit la paire {archive_name}.dat/dir")

            for archive_name in ("ED6_DT21", "ED6_DT22", "ED6_DT30", "ED6_DT3C"):
                command([archive_tool, "verify", str(job / f"{archive_name}.dir")], job, 900)

            write_status(job, "verify", 84, "Vérification par ré-extraction")
            verify = job / "verify"
            command([
                archive_tool, "extract", "--output", str(verify),
                str(job / "ED6_DT21.dir"),
            ], job, 900)
            verify_lexicon = job / "verify_lexicon"
            command([
                archive_tool, "extract", "--output", str(verify_lexicon),
                str(job / "ED6_DT22.dir"),
            ], job, 900)
            verify_monsters = job / "verify_monsters"
            command([
                archive_tool, "extract", "--output", str(verify_monsters),
                str(job / "ED6_DT3C.dir"),
            ], job, 900)
            for position, script_row in enumerate(scripts, 1):
                code = str(script_row["code"]).lower()
                original = compiled / f"{code}._sn"
                extracted = locate_extracted(verify, f"{code}._sn")
                if digest(original) != digest(extracted):
                    raise RuntimeError(f"Roundtrip archive différent pour {code}._sn")
                if position % 50 == 0 or position == len(scripts):
                    progress = 84 + int(position * 12 / max(1, len(scripts)))
                    write_status(job, "verify", progress, f"Vérification : {position}/{len(scripts)} fichiers")

            for original in sorted(compiled_lexicon.glob("*._dt")):
                extracted = locate_extracted(verify_lexicon, original.name)
                if digest(original) != digest(extracted):
                    raise RuntimeError(f"Roundtrip archive différent pour {original.name}")
            verify_battle = job / "verify_battle"
            command([
                archive_tool, "extract", "--output", str(verify_battle),
                str(job / "ED6_DT30.dir"),
            ], job, 900)
            for original in sorted(compiled_battle.glob("*._dt")):
                if digest(original) != digest(locate_extracted(verify_battle, original.name)):
                    raise RuntimeError(f"Roundtrip archive différent pour {original.name}")
            extracted_monster_note = locate_extracted(verify_monsters, compiled_monster_note.name)
            if digest(compiled_monster_note) != digest(extracted_monster_note):
                raise RuntimeError(f"Roundtrip archive différent pour {compiled_monster_note.name}")

            with patch:
                for archive_name in ("ED6_DT21", "ED6_DT22", "ED6_DT30", "ED6_DT3C"):
                    patch.write(job / f"{archive_name}.dir", f"{archive_name}.dir")
                    patch.write(job / f"{archive_name}.dat", f"{archive_name}.dat")
                for executable in ("ed6_win3.exe", "ed6_win3_DX9.exe"):
                    patch.write(compiled_exe / executable, executable)
                patch.write(log_path, "build.log")
            (job / "Sky3rd-test-patch.zip.part").replace(job / "Sky3rd-test-patch.zip")
            write_status(job, "complete", 100, "Compilation et vérification terminées" + (
                f" ; laissés en anglais : {', '.join(sorted(left_in_english))}" if left_in_english else ""
            ))
            return 0
        except Exception as error:
            traceback.print_exc()
            print(f"ERREUR: {error}")
            write_status(job, "failed", 100, str(error), error=True)
            return 1
        finally:
            # A patch left half written is closed before the clean-up deletes it.
            if "patch" in locals() and patch.fp is not None:
                patch.close()
            # Success or failure, the intermediate files (several GB) go.
            try:
                clean_job(job)
            except OSError as error:
                print(f"Nettoyage incomplet : {error}")
            sys.stdout, sys.stderr = original_stdout, original_stderr


if __name__ == "__main__":
    raise SystemExit(main())
