"""Patch — multi-file patch application and diff generation for Skills.

Consolidated from OpenSpace:
  - openspace/skill_engine/patch.py
  - openspace/skill_engine/fuzzy_match.py
  - openspace/skill_engine/skill_utils.py (partial)

Three LLM output formats:
  FULL  — Complete file content (single or multi-file via *** Begin Files)
  DIFF  — SEARCH/REPLACE blocks (single-file)
  PATCH — *** Begin Patch multi-file format (Add/Update/Delete)

Three operations:
  fix_skill    — in-place repair of an existing skill directory
  derive_skill — copy directory → apply changes in the copy
  create_skill — create a brand-new skill directory
"""
from __future__ import annotations

import difflib
import re
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

# ─── Constants ───────────────────────────────────────────────────────────────

SKILL_FILENAME = "SKILL.md"
_SKILL_ID_FILENAME = ".skill_id"


# ─── Data types ──────────────────────────────────────────────────────────────

class PatchType(str, Enum):
    AUTO = "auto"
    FULL = "full"
    DIFF = "diff"
    PATCH = "patch"


@dataclass
class UpdateChunk:
    old_lines: List[str]
    new_lines: List[str]
    change_context: Optional[str] = None
    is_end_of_file: bool = False


@dataclass
class PatchHunk:
    type: str  # "add" | "update" | "delete"
    path: str
    contents: str = ""
    move_path: Optional[str] = None
    chunks: List[UpdateChunk] = field(default_factory=list)


@dataclass
class PatchResult:
    hunks: List[PatchHunk]


@dataclass
class SkillEditResult:
    skill_dir: Path = field(default_factory=lambda: Path("."))
    content_diff: str = ""
    content_snapshot: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class PatchError(RuntimeError):
    pass


class PatchParseError(PatchError):
    pass


# ─── Skill utilities (from skill_utils.py) ───────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def parse_frontmatter(content: str) -> Dict[str, str]:
    if not content.startswith("---"):
        return {}
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}
    fm: Dict[str, str] = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            if key:
                fm[key] = value.strip()
    return fm


def get_frontmatter_field(content: str, field_name: str) -> Optional[str]:
    fm = parse_frontmatter(content)
    return fm.get(field_name)


def set_frontmatter_field(content: str, field_name: str, value: str) -> str:
    if not content.startswith("---"):
        return f"---\n{field_name}: {value}\n---\n{content}"
    end = content.index("---", 3)
    fm_block = content[3:end]
    if f"{field_name}:" in fm_block:
        lines = fm_block.split("\n")
        for i, line in enumerate(lines):
            if line.startswith(f"{field_name}:"):
                lines[i] = f"{field_name}: {value}"
                break
        return content[:3] + "\n".join(lines) + content[end:]
    else:
        return content[:3] + fm_block + f"{field_name}: {value}\n" + content[3:]


def extract_change_summary(content: str) -> Tuple[str, str]:
    """Parse CHANGE_SUMMARY: from first line. Returns (clean_content, summary)."""
    lines = content.split("\n", 1)
    if lines and lines[0].startswith("CHANGE_SUMMARY:"):
        summary = lines[0].split(":", 1)[1].strip()
        return (lines[1].strip() if len(lines) > 1 else "", summary)
    return (content, "")


def strip_markdown_fences(text: str) -> str:
    """Remove ```markdown / ``` wrapping."""
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.index("\n") if "\n" in text else -1
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return text


def validate_skill_dir(skill_dir: Path) -> Optional[str]:
    """Validate a skill directory. Returns error string or None."""
    if not skill_dir.is_dir():
        return f"Not a directory: {skill_dir}"
    skill_file = skill_dir / SKILL_FILENAME
    if not skill_file.exists():
        return f"{SKILL_FILENAME} not found: {skill_file}"
    content = skill_file.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)
    if "name" not in fm:
        return f"{SKILL_FILENAME} missing 'name' in frontmatter"
    return None


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n... [truncated]"


# ─── Fuzzy matching (from fuzzy_match.py) ────────────────────────────────────

def levenshtein(a: str, b: str) -> int:
    if not a or not b:
        return max(len(a), len(b))
    rows, cols = len(a) + 1, len(b) + 1
    matrix = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        matrix[i][0] = i
    for j in range(cols):
        matrix[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            matrix[i][j] = min(
                matrix[i - 1][j] + 1,
                matrix[i][j - 1] + 1,
                matrix[i - 1][j - 1] + cost,
            )
    return matrix[len(a)][len(b)]


def _simple_replacer(_content: str, find: str):
    yield find


def _line_trimmed_replacer(content: str, find: str):
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if not search_lines:
        return
    n = len(search_lines)
    for i in range(len(original_lines) - n + 1):
        if all(original_lines[i + j].strip() == search_lines[j].strip() for j in range(n)):
            start = sum(len(original_lines[k]) + 1 for k in range(i))
            end = start
            for k in range(n):
                end += len(original_lines[i + k])
                if k < n - 1:
                    end += 1
            yield content[start:end]


def _block_anchor_replacer(content: str, find: str):
    original_lines = content.split("\n")
    search_lines = find.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    if len(search_lines) < 3:
        return
    first_s, last_s = search_lines[0].strip(), search_lines[-1].strip()
    candidates = []
    for i, line in enumerate(original_lines):
        if line.strip() != first_s:
            continue
        for j in range(i + 2, len(original_lines)):
            if original_lines[j].strip() == last_s:
                candidates.append((i, j))
                break
    if not candidates:
        return

    def _extract(sl, el):
        s = sum(len(original_lines[k]) + 1 for k in range(sl))
        e = s
        for k in range(sl, el + 1):
            e += len(original_lines[k])
            if k < el:
                e += 1
        return content[s:e]

    if len(candidates) == 1:
        sl, el = candidates[0]
        yield _extract(sl, el)
        return
    best, best_sim = None, -1.0
    for sl, el in candidates:
        actual_size = el - sl + 1
        lines_to_check = min(len(search_lines) - 2, actual_size - 2)
        if lines_to_check > 0:
            raw = 0.0
            for j in range(1, min(len(search_lines) - 1, actual_size - 1)):
                ol = original_lines[sl + j].strip()
                sr = search_lines[j].strip()
                ml = max(len(ol), len(sr))
                if ml:
                    raw += 1 - levenshtein(ol, sr) / ml
            sim = raw / lines_to_check
        else:
            sim = 1.0
        if sim > best_sim:
            best_sim = sim
            best = (sl, el)
    if best_sim >= 0.3 and best:
        yield _extract(best[0], best[1])


def _whitespace_normalized_replacer(content: str, find: str):
    def _norm(t):
        return re.sub(r"\s+", " ", t).strip()
    nf = _norm(find)
    lines = content.split("\n")
    for line in lines:
        if _norm(line) == nf:
            yield line
    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i:i + len(find_lines)])
            if _norm(block) == nf:
                yield block


def _indentation_flexible_replacer(content: str, find: str):
    def _remove_indent(t):
        lines = t.split("\n")
        non_empty = [l for l in lines if l.strip()]
        if not non_empty:
            return t
        mi = min(len(l) - len(l.lstrip()) for l in non_empty)
        return "\n".join(l[mi:] if l.strip() else l for l in lines)
    nf = _remove_indent(find)
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(content_lines) - len(find_lines) + 1):
        block = "\n".join(content_lines[i:i + len(find_lines)])
        if _remove_indent(block) == nf:
            yield block


def _trimmed_boundary_replacer(content: str, find: str):
    trimmed = find.strip()
    if trimmed == find:
        return
    if trimmed in content:
        yield trimmed
    lines = content.split("\n")
    fl = find.split("\n")
    for i in range(len(lines) - len(fl) + 1):
        block = "\n".join(lines[i:i + len(fl)])
        if block.strip() == trimmed:
            yield block


_REPLACER_CHAIN = [
    ("simple", _simple_replacer),
    ("line_trimmed", _line_trimmed_replacer),
    ("block_anchor", _block_anchor_replacer),
    ("whitespace_normalized", _whitespace_normalized_replacer),
    ("indentation_flexible", _indentation_flexible_replacer),
    ("trimmed_boundary", _trimmed_boundary_replacer),
]


def fuzzy_find_match(content: str, find: str) -> Tuple[str, int]:
    for name, replacer in _REPLACER_CHAIN:
        for candidate in replacer(content, find):
            pos = content.find(candidate)
            if pos != -1:
                return candidate, pos
    return "", -1


# ─── Patch format detection ──────────────────────────────────────────────────

PATCH_PATTERN = re.compile(
    r"<{7}\s*SEARCH\s*\n(.*?)\n\s*={7}\s*\n(.*?)\n\s*>{7}\s*REPLACE\s*",
    re.DOTALL,
)
_FILE_HEADER_RE = re.compile(r"^\*\*\*\s*File:\s*(.+)$", re.MULTILINE)


def detect_patch_type(content: str) -> PatchType:
    if "*** Begin Patch" in content:
        return PatchType.PATCH
    if "*** Begin Files" in content:
        return PatchType.FULL
    if _FILE_HEADER_RE.findall(content):
        return PatchType.FULL
    if "<<<<<<< SEARCH" in content:
        return PatchType.DIFF
    return PatchType.FULL


# ─── Multi-file FULL format ──────────────────────────────────────────────────

def parse_multi_file_full(content: str) -> Dict[str, str]:
    stripped = content.strip()
    if stripped.startswith("*** Begin Files"):
        stripped = stripped[len("*** Begin Files"):].strip()
    end_idx = stripped.rfind("*** End Files")
    if end_idx != -1:
        stripped = stripped[:end_idx].strip()
    headers = list(_FILE_HEADER_RE.finditer(stripped))
    if not headers:
        return {SKILL_FILENAME: content}
    files: Dict[str, str] = {}
    for i, match in enumerate(headers):
        fp = match.group(1).strip()
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(stripped)
        fc = stripped[start:end].strip("\n")
        if fc and not fc.endswith("\n"):
            fc += "\n"
        files[fp] = fc
    return files


def _apply_multi_file_full(content: str, skill_dir: Path) -> None:
    files = parse_multi_file_full(content)
    for rel_path, fc in files.items():
        target = (skill_dir / rel_path).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            raise PatchError(f"Path escapes skill directory: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fc, encoding="utf-8")


# ─── Multi-file PATCH format ────────────────────────────────────────────────

Comparator = Callable[[str, str], bool]


def _try_match(lines, pattern, start_index, compare, eof):
    n, p = len(lines), len(pattern)
    if p == 0:
        return -1
    if eof:
        from_end = n - p
        if from_end >= start_index:
            if all(compare(lines[from_end + j], pattern[j]) for j in range(p)):
                return from_end
    for i in range(start_index, n - p + 1):
        if all(compare(lines[i + j], pattern[j]) for j in range(p)):
            return i
    return -1


_UNICODE_REPLACEMENTS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "…": "...", " ": " ",
}
_UNICODE_RE = re.compile("|".join(re.escape(k) for k in _UNICODE_REPLACEMENTS))


def _normalize_unicode(s: str) -> str:
    return _UNICODE_RE.sub(lambda m: _UNICODE_REPLACEMENTS[m.group()], s)


def seek_sequence(lines, pattern, start_index, eof=False):
    if not pattern:
        return -1
    idx = _try_match(lines, pattern, start_index, lambda a, b: a == b, eof)
    if idx != -1:
        return idx
    idx = _try_match(lines, pattern, start_index, lambda a, b: a.rstrip() == b.rstrip(), eof)
    if idx != -1:
        return idx
    idx = _try_match(lines, pattern, start_index, lambda a, b: a.strip() == b.strip(), eof)
    if idx != -1:
        return idx
    return _try_match(
        lines, pattern, start_index,
        lambda a, b: _normalize_unicode(a.strip()) == _normalize_unicode(b.strip()), eof,
    )


def _parse_patch_header(lines, idx):
    line = lines[idx]
    if line.startswith("*** Add File:"):
        fp = line.split(":", 1)[1].strip()
        return (fp, None, idx + 1) if fp else None
    if line.startswith("*** Delete File:"):
        fp = line.split(":", 1)[1].strip()
        return (fp, None, idx + 1) if fp else None
    if line.startswith("*** Update File:"):
        fp = line.split(":", 1)[1].strip()
        if not fp:
            return None
        move_path = None
        ni = idx + 1
        if ni < len(lines) and lines[ni].startswith("*** Move to:"):
            move_path = lines[ni].split(":", 1)[1].strip()
            ni += 1
        return (fp, move_path, ni)
    return None


def _parse_add_file_content(lines, start_idx):
    content_lines = []
    i = start_idx
    while i < len(lines) and not lines[i].startswith("***"):
        if lines[i].startswith("+"):
            content_lines.append(lines[i][1:])
        i += 1
    content = "\n".join(content_lines)
    if content.endswith("\n"):
        content = content[:-1]
    return content, i


def _parse_update_chunks(lines, start_idx):
    chunks = []
    i = start_idx
    while i < len(lines) and not lines[i].startswith("***"):
        if lines[i].startswith("@@"):
            ctx = lines[i][2:].strip()
            i += 1
            old_lines, new_lines = [], []
            is_eof = False
            while i < len(lines):
                cl = lines[i]
                if cl.startswith("@@"):
                    break
                if cl.startswith("***") and cl != "*** End of File":
                    break
                if cl == "*** End of File":
                    is_eof = True
                    i += 1
                    break
                if cl.startswith(" "):
                    old_lines.append(cl[1:])
                    new_lines.append(cl[1:])
                elif cl.startswith("-"):
                    old_lines.append(cl[1:])
                elif cl.startswith("+"):
                    new_lines.append(cl[1:])
                i += 1
            chunks.append(UpdateChunk(
                old_lines=old_lines, new_lines=new_lines,
                change_context=ctx or None, is_end_of_file=is_eof,
            ))
        else:
            i += 1
    return chunks, i


def parse_patch(patch_text: str) -> PatchResult:
    lines = patch_text.strip().split("\n")
    begin_idx = end_idx = -1
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "*** Begin Patch":
            begin_idx = i
        elif s == "*** End Patch":
            end_idx = i
    if begin_idx == -1 or end_idx == -1 or begin_idx >= end_idx:
        raise PatchParseError("Invalid patch: missing *** Begin Patch / *** End Patch")
    hunks = []
    i = begin_idx + 1
    while i < end_idx:
        header = _parse_patch_header(lines, i)
        if header is None:
            i += 1
            continue
        fp, move_path, ni = header
        if lines[i].startswith("*** Add File:"):
            content, ni = _parse_add_file_content(lines, ni)
            hunks.append(PatchHunk(type="add", path=fp, contents=content))
            i = ni
        elif lines[i].startswith("*** Delete File:"):
            hunks.append(PatchHunk(type="delete", path=fp))
            i = ni
        elif lines[i].startswith("*** Update File:"):
            chunks, ni = _parse_update_chunks(lines, ni)
            hunks.append(PatchHunk(type="update", path=fp, move_path=move_path, chunks=chunks))
            i = ni
        else:
            i += 1
    return PatchResult(hunks=hunks)


def _compute_replacements(original_lines, file_path, chunks):
    replacements = []
    line_index = 0
    for chunk in chunks:
        if chunk.change_context:
            ctx_idx = seek_sequence(original_lines, [chunk.change_context], line_index)
            if ctx_idx == -1:
                raise PatchError(f"Cannot locate anchor '{chunk.change_context}' in {file_path}")
            line_index = ctx_idx
        if not chunk.old_lines:
            insert_idx = len(original_lines) - 1 if (original_lines and original_lines[-1] == "") else len(original_lines)
            replacements.append((insert_idx, 0, chunk.new_lines))
            continue
        pattern = list(chunk.old_lines)
        new_slice = list(chunk.new_lines)
        found = seek_sequence(original_lines, pattern, line_index, chunk.is_end_of_file)
        if found == -1 and pattern and pattern[-1] == "":
            pattern = pattern[:-1]
            if new_slice and new_slice[-1] == "":
                new_slice = new_slice[:-1]
            found = seek_sequence(original_lines, pattern, line_index, chunk.is_end_of_file)
        if found != -1:
            replacements.append((found, len(pattern), new_slice))
            line_index = found + len(pattern)
        else:
            raise PatchError(f"Cannot find expected lines in {file_path}:\n" + "\n".join(chunk.old_lines))
    replacements.sort(key=lambda x: x[0])
    return replacements


def _apply_replacements(lines, replacements):
    result = list(lines)
    for start_idx, old_len, new_seg in reversed(replacements):
        del result[start_idx:start_idx + old_len]
        for j, line in enumerate(new_seg):
            result.insert(start_idx + j, line)
    return result


def apply_update_chunks(file_path: str, original_content: str, chunks: List[UpdateChunk]) -> str:
    original_lines = original_content.split("\n")
    if original_lines and original_lines[-1] == "":
        original_lines.pop()
    replacements = _compute_replacements(original_lines, file_path, chunks)
    new_lines = _apply_replacements(original_lines, replacements)
    if not new_lines or new_lines[-1] != "":
        new_lines.append("")
    return "\n".join(new_lines)


def _apply_multi_file_patch(patch_text: str, skill_dir: Path) -> None:
    parsed = parse_patch(patch_text)
    if not parsed.hunks:
        raise PatchParseError("Patch contains no file operations")
    resolved = skill_dir.resolve()
    changes = []
    for hunk in parsed.hunks:
        abs_path = (skill_dir / hunk.path).resolve()
        if not str(abs_path).startswith(str(resolved)):
            raise PatchError(f"Path escapes skill directory: {hunk.path}")
        if hunk.type == "add":
            nc = hunk.contents
            if nc and not nc.endswith("\n"):
                nc += "\n"
            changes.append(("add", abs_path, "", nc))
        elif hunk.type == "delete":
            if not abs_path.exists():
                raise PatchError(f"Cannot delete non-existent file: {hunk.path}")
            changes.append(("delete", abs_path, "", ""))
        elif hunk.type == "update":
            if not abs_path.exists():
                raise PatchError(f"Cannot update non-existent file: {hunk.path}")
            old = abs_path.read_text(encoding="utf-8")
            new = apply_update_chunks(str(hunk.path), old, hunk.chunks)
            changes.append(("update", abs_path, old, new))
    for ct, ap, _, nc in changes:
        if ct == "add":
            ap.parent.mkdir(parents=True, exist_ok=True)
            ap.write_text(nc, encoding="utf-8")
        elif ct == "delete":
            if ap.exists():
                ap.unlink()
        elif ct == "update":
            ap.write_text(nc, encoding="utf-8")


# ─── SEARCH/REPLACE (DIFF format) ───────────────────────────────────────────

def _strip_trailing_ws(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines())


def _find_similar_lines(search_line, text, max_suggestions=3):
    search_clean = search_line.strip()
    if not search_clean:
        return []
    results = []
    for i, line in enumerate(text.splitlines()):
        lc = line.strip()
        if not lc:
            continue
        ratio = difflib.SequenceMatcher(None, search_clean, lc).ratio()
        if ratio > 0.6:
            results.append((line, i + 1, ratio))
    results.sort(key=lambda x: x[2], reverse=True)
    return [(l, n) for l, n, _ in results[:max_suggestions]]


def apply_search_replace(
    patch_text: str, original: str, *, strict: bool = True,
) -> Tuple[str, int, Optional[str]]:
    new_text = original
    num_applied = 0
    blocks = list(PATCH_PATTERN.finditer(patch_text))
    if not blocks:
        return new_text, 0, None
    for block in blocks:
        search = _strip_trailing_ws(block.group(1))
        replace = _strip_trailing_ws(block.group(2))
        if not search.strip():
            new_text = new_text.rstrip("\n") + "\n" + replace + "\n"
            num_applied += 1
            continue
        matched, pos = fuzzy_find_match(new_text, search)
        if pos != -1:
            new_text = new_text[:pos] + replace + new_text[pos + len(matched):]
            num_applied += 1
            continue
        if strict:
            first_line = search.splitlines()[0].strip() if search.splitlines() else ""
            similar = _find_similar_lines(first_line, new_text)
            msg = [f"SEARCH text not found in {SKILL_FILENAME}", "", f"Looking for: {first_line!r}"]
            if similar:
                msg += ["", "Similar lines found:"]
                for l, n in similar:
                    msg.append(f"  Line {n}: {l.strip()}")
            msg += ["", "Ensure the SEARCH block matches the file content exactly."]
            return new_text, num_applied, "\n".join(msg)
    return new_text, num_applied, None


def _apply_search_replace_to_file(patch_text: str, skill_file: Path) -> None:
    original = skill_file.read_text(encoding="utf-8")
    updated, num_applied, error = apply_search_replace(patch_text, original)
    if error:
        raise PatchError(error)
    if num_applied == 0:
        raise PatchError("No SEARCH/REPLACE blocks found in LLM output")
    skill_file.write_text(updated, encoding="utf-8")


# ─── Diff & snapshot ────────────────────────────────────────────────────────

def compute_unified_diff(original: str, updated: str, *, filename: str = SKILL_FILENAME, context: int = 3) -> str:
    diff_lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{filename}", tofile=f"b/{filename}", n=context,
    )
    return "".join(diff_lines)


def compute_skill_diff(old_dir: Path, new_dir: Path) -> str:
    old_files = _collect_files(old_dir) if old_dir.is_dir() else {}
    new_files = _collect_files(new_dir) if new_dir.is_dir() else {}
    all_names = sorted(set(old_files) | set(new_files))
    parts = []
    for name in all_names:
        d = compute_unified_diff(old_files.get(name, ""), new_files.get(name, ""), filename=name)
        if d:
            parts.append(d)
    return "\n".join(parts)


def collect_skill_snapshot(skill_dir: Path) -> Dict[str, str]:
    return _collect_files(skill_dir)


def _compute_files_diff(old_files: Dict[str, str], new_files: Dict[str, str]) -> str:
    all_names = sorted(set(old_files) | set(new_files))
    parts = []
    for name in all_names:
        d = compute_unified_diff(old_files.get(name, ""), new_files.get(name, ""), filename=name)
        if d:
            parts.append(d)
    return "\n".join(parts)


def _collect_files(directory: Path) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file() and p.name != _SKILL_ID_FILENAME:
            rel = str(p.relative_to(directory))
            try:
                files[rel] = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                pass
    return files


# ─── Three main operations ──────────────────────────────────────────────────

def fix_skill(
    skill_dir: Path, content: str, patch_type: PatchType = PatchType.AUTO,
) -> SkillEditResult:
    if not skill_dir.is_dir():
        return SkillEditResult(error=f"Skill directory not found: {skill_dir}")
    skill_file = skill_dir / SKILL_FILENAME
    if not skill_file.exists():
        return SkillEditResult(error=f"{SKILL_FILENAME} not found: {skill_file}")

    old_files = _collect_files(skill_dir)
    if patch_type == PatchType.AUTO:
        patch_type = detect_patch_type(content)
    try:
        if patch_type == PatchType.PATCH:
            _apply_multi_file_patch(content, skill_dir)
        elif patch_type == PatchType.FULL:
            _apply_multi_file_full(content, skill_dir)
        elif patch_type == PatchType.DIFF:
            _apply_search_replace_to_file(content, skill_file)
        else:
            return SkillEditResult(error=f"Unknown patch type: {patch_type}")
    except (PatchError, Exception) as e:
        return SkillEditResult(error=str(e))

    new_files = _collect_files(skill_dir)
    diff = _compute_files_diff(old_files, new_files)
    return SkillEditResult(skill_dir=skill_dir, content_diff=diff, content_snapshot=new_files)


def derive_skill(
    source_dirs: Union[Path, List[Path]],
    target_dir: Path,
    content: str,
    patch_type: PatchType = PatchType.AUTO,
) -> SkillEditResult:
    sources = [source_dirs] if isinstance(source_dirs, Path) else list(source_dirs)
    if not sources:
        return SkillEditResult(error="derive_skill requires at least one source directory")
    if target_dir.exists():
        return SkillEditResult(error=f"Target already exists: {target_dir}")
    for sd in sources:
        if not sd.is_dir():
            return SkillEditResult(error=f"Source does not exist: {sd}")
        if not (sd / SKILL_FILENAME).exists():
            return SkillEditResult(error=f"Source {SKILL_FILENAME} not found: {sd / SKILL_FILENAME}")

    first_source = sources[0]
    is_multi = len(sources) > 1

    if is_multi:
        if patch_type == PatchType.AUTO:
            patch_type = detect_patch_type(content)
        if patch_type == PatchType.DIFF:
            patch_type = PatchType.FULL
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if patch_type == PatchType.PATCH:
                _apply_multi_file_patch(content, target_dir)
            else:
                _apply_multi_file_full(content, target_dir)
        except (PatchError, Exception) as e:
            shutil.rmtree(target_dir, ignore_errors=True)
            return SkillEditResult(error=str(e))
    else:
        shutil.copytree(first_source, target_dir)
        if patch_type == PatchType.AUTO:
            patch_type = detect_patch_type(content)
        try:
            if patch_type == PatchType.PATCH:
                _apply_multi_file_patch(content, target_dir)
            elif patch_type == PatchType.FULL:
                _apply_multi_file_full(content, target_dir)
            elif patch_type == PatchType.DIFF:
                _apply_search_replace_to_file(content, target_dir / SKILL_FILENAME)
            else:
                shutil.rmtree(target_dir, ignore_errors=True)
                return SkillEditResult(error=f"Unknown patch type: {patch_type}")
        except (PatchError, Exception) as e:
            shutil.rmtree(target_dir, ignore_errors=True)
            return SkillEditResult(error=str(e))

    new_files = _collect_files(target_dir)
    diff = compute_skill_diff(first_source, target_dir) if not is_multi else ""
    return SkillEditResult(skill_dir=target_dir, content_diff=diff, content_snapshot=new_files)


def create_skill(
    target_dir: Path, content: str, patch_type: PatchType = PatchType.AUTO,
) -> SkillEditResult:
    if target_dir.exists():
        return SkillEditResult(error=f"Target already exists: {target_dir}")
    if patch_type == PatchType.AUTO:
        patch_type = detect_patch_type(content)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        if patch_type == PatchType.PATCH:
            _apply_multi_file_patch(content, target_dir)
        elif patch_type == PatchType.FULL:
            _apply_multi_file_full(content, target_dir)
        elif patch_type == PatchType.DIFF:
            (target_dir / SKILL_FILENAME).write_text(content, encoding="utf-8")
        else:
            shutil.rmtree(target_dir, ignore_errors=True)
            return SkillEditResult(error=f"Unknown patch type: {patch_type}")
    except (PatchError, Exception) as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        return SkillEditResult(error=str(e))

    new_files = _collect_files(target_dir)
    add_all = "\n".join(
        compute_unified_diff("", text, filename=name)
        for name, text in sorted(new_files.items())
        if compute_unified_diff("", text, filename=name)
    )
    return SkillEditResult(skill_dir=target_dir, content_diff=add_all, content_snapshot=new_files)
