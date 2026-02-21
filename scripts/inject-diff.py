#!/usr/bin/env python3
"""Inject git diff content into HTML report placeholder <script> tags.

Usage:
    # Pairs-file mode (preferred — supports line ranges):
    python3 inject-diff.py <html_file> <worktree_path> <merge_base> --pairs-file <pairs_file>

    # Legacy CLI-arg mode (backward compat — no line range support):
    python3 inject-diff.py <html_file> <worktree_path> <merge_base> <id1>:<file1> [<id2>:<file2> ...]

For each id/file pair, runs `git diff -w <merge_base>..HEAD -- <file>` in the
worktree, escapes `</script` sequences, and replaces the empty
`<script type="application/diff" data-for="ID"></script>` tag with one
containing the diff output.

When a line range is provided (pairs-file mode), the diff is filtered to only
include hunks that overlap with the target lines (plus padding). This keeps
the HTML report focused on the code relevant to each issue.

Diffs are cached by file path so multiple issues referencing the same file
only trigger a single git call.
"""

from __future__ import annotations

import re
import subprocess
import sys

CONTEXT_PADDING = 5
TRIM_THRESHOLD_FACTOR = 3


def usage():
    print(
        f"Usage: {sys.argv[0]} <html_file> <worktree_path> <merge_base> "
        f"[--pairs-file <file> | <id>:<file> ...]",
        file=sys.stderr,
    )
    sys.exit(1)


def get_diff(worktree_path: str, merge_base: str, file_path: str) -> str:
    """Run git diff for a single file and return the raw output."""
    result = subprocess.run(
        ["git", "-C", worktree_path, "diff", "-w", f"{merge_base}..HEAD", "--", file_path],
        capture_output=True, text=True,
    )
    return result.stdout


def escape_script_close(text: str) -> str:
    """Replace </script (case-insensitive) with <\\/script to prevent premature tag closure."""
    return re.sub(r'</script', r'<\\/script', text, flags=re.IGNORECASE)


def parse_pairs_file(path: str) -> list[tuple[str, str, int | None, int | None]]:
    """Read a tab-separated pairs file.

    Format per line: id<TAB>file[<TAB>start-end]
    Blank lines and lines starting with # are skipped.

    Returns list of (id, file, start_line|None, end_line|None).
    """
    pairs = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) < 2 or len(fields) > 3:
                print(f"Error: pairs file line {lineno}: expected 2 or 3 tab-separated fields, "
                      f"got {len(fields)}", file=sys.stderr)
                sys.exit(1)
            diff_id = fields[0]
            file_path = fields[1]
            if not diff_id:
                print(f"Error: pairs file line {lineno}: empty id field", file=sys.stderr)
                sys.exit(1)
            if not file_path:
                print(f"Error: pairs file line {lineno}: empty file field", file=sys.stderr)
                sys.exit(1)
            start_line = None
            end_line = None
            if len(fields) == 3 and fields[2]:
                range_match = re.fullmatch(r"(\d+)-(\d+)", fields[2])
                if not range_match:
                    print(f"Error: pairs file line {lineno}: invalid line range {fields[2]!r}, "
                          f"expected N-N", file=sys.stderr)
                    sys.exit(1)
                start_line = int(range_match.group(1))
                end_line = int(range_match.group(2))
                if start_line > end_line:
                    print(f"Error: pairs file line {lineno}: start ({start_line}) > end ({end_line})",
                          file=sys.stderr)
                    sys.exit(1)
            pairs.append((diff_id, file_path, start_line, end_line))
    return pairs


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_hunk_header(line: str) -> tuple[int, int, int, int] | None:
    """Parse a unified diff @@ header line.

    Returns (old_start, old_count, new_start, new_count) or None if not a hunk header.
    Omitted counts default to 1.
    """
    m = _HUNK_HEADER_RE.match(line)
    if not m:
        return None
    old_start = int(m.group(1))
    old_count = int(m.group(2)) if m.group(2) is not None else 1
    new_start = int(m.group(3))
    new_count = int(m.group(4)) if m.group(4) is not None else 1
    return (old_start, old_count, new_start, new_count)


def trim_hunk_to_range(
    header_line: str, body: list[str],
    old_start: int, new_start: int,
    target_start: int, target_end: int,
) -> tuple[str, list[str]]:
    """Trim a hunk's body to only the lines overlapping [target_start, target_end].

    target_start/target_end are new-file line numbers (already padded by caller).
    Returns (new_header, trimmed_body).
    """
    # Step 1: Annotate each body line with cursor values BEFORE processing
    annotated: list[tuple[str, int, int]] = []  # (line_text, cur_new_before, cur_old_before)
    cur_new = new_start
    cur_old = old_start
    for line in body:
        annotated.append((line, cur_new, cur_old))
        if line.startswith("+"):
            cur_new += 1
        elif line.startswith("-"):
            cur_old += 1
        elif line.startswith("\\"):
            pass  # no-newline marker, advances neither
        else:
            # context line (space prefix or empty)
            cur_new += 1
            cur_old += 1

    # Step 2: Find first/last body indices where cur_new_before is in range
    # Only consider + and context lines (not - or \ lines)
    first_idx = None
    last_idx = None
    for idx, (line, cn, _co) in enumerate(annotated):
        if line.startswith("-") or line.startswith("\\"):
            continue
        if target_start <= cn <= target_end:
            if first_idx is None:
                first_idx = idx
            last_idx = idx

    if first_idx is None:
        # No overlap found — return unchanged
        return header_line, body

    slice_start = first_idx
    slice_end = last_idx

    # Step 3: Boundary cleanup
    # Walk slice_start backward to include preceding '-' lines (deletion side of a change block)
    while slice_start > 0 and body[slice_start - 1].startswith("-"):
        slice_start -= 1

    # Include trailing '\ No newline at end of file'
    if slice_end + 1 < len(body) and body[slice_end + 1].startswith("\\"):
        slice_end += 1

    # Step 4: Compute counts from trimmed body
    trimmed_body = body[slice_start:slice_end + 1]
    trimmed_new_count = 0
    trimmed_old_count = 0
    for line in trimmed_body:
        if line.startswith("+"):
            trimmed_new_count += 1
        elif line.startswith("-"):
            trimmed_old_count += 1
        elif line.startswith("\\"):
            pass
        else:
            # context line
            trimmed_new_count += 1
            trimmed_old_count += 1

    # Step 5: Derive @@ header starts from cursor values at slice_start
    trimmed_new_start = annotated[slice_start][1]  # cur_new_before
    trimmed_old_start = annotated[slice_start][2]  # cur_old_before

    # Zero-count adjustment per unified diff semantics
    if trimmed_old_count == 0:
        trimmed_old_start = max(0, trimmed_old_start - 1)
    if trimmed_new_count == 0:
        trimmed_new_start = max(0, trimmed_new_start - 1)

    # Step 6: Return new header and trimmed body
    new_header = f"@@ -{trimmed_old_start},{trimmed_old_count} +{trimmed_new_start},{trimmed_new_count} @@"
    return new_header, trimmed_body


def filter_diff_hunks(raw_diff: str, start_line: int, end_line: int) -> str:
    """Filter a unified diff to only include hunks overlapping the target line range.

    Lines are new-file (HEAD) line numbers. A padding of CONTEXT_PADDING lines is
    added on each side of the target range.

    If no hunks overlap, falls back to the single nearest hunk.
    Returns the filtered diff as a valid unified diff string.
    """
    if not raw_diff:
        return raw_diff

    lines = raw_diff.split("\n")

    # Split into file header and hunk groups
    file_header_lines: list[str] = []
    hunks: list[tuple[str, list[str], int, int]] = []  # (header, body, new_start, new_count)

    # Collect file header (everything before first @@)
    i = 0
    while i < len(lines):
        if lines[i].startswith("@@"):
            break
        file_header_lines.append(lines[i])
        i += 1

    # No hunks at all (binary/empty) — return raw diff unchanged
    if i >= len(lines):
        return raw_diff

    # Collect hunks
    while i < len(lines):
        if not lines[i].startswith("@@"):
            i += 1
            continue
        header_line = lines[i]
        parsed = parse_hunk_header(header_line)
        if parsed is None:
            i += 1
            continue
        _, _, new_start, new_count = parsed
        body: list[str] = []
        i += 1
        while i < len(lines) and not lines[i].startswith("@@"):
            body.append(lines[i])
            i += 1
        hunks.append((header_line, body, new_start, new_count))

    if not hunks:
        return raw_diff

    # Pad the target range
    padded_start = max(1, start_line - CONTEXT_PADDING)
    padded_end = end_line + CONTEXT_PADDING

    # Check overlap for each hunk
    selected_indices: list[int] = []
    for idx, (_, _, new_start, new_count) in enumerate(hunks):
        hunk_start = new_start
        hunk_end = (new_start + new_count - 1) if new_count > 0 else new_start
        if hunk_start <= padded_end and hunk_end >= padded_start:
            selected_indices.append(idx)

    # Fallback: nearest hunk if zero overlap
    if not selected_indices:
        best_idx = 0
        best_dist = float("inf")
        for idx, (_, _, new_start, new_count) in enumerate(hunks):
            hunk_start = new_start
            hunk_end = (new_start + new_count - 1) if new_count > 0 else new_start
            dist = max(0, hunk_start - end_line, start_line - hunk_end)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        selected_indices = [best_idx]

    # Reconstruct: file header + selected hunks in original order
    result_lines = list(file_header_lines)
    for idx in selected_indices:
        header_line, body, _, _ = hunks[idx]

        # Trim large hunks to the target range
        body_size = len(body)
        target_span = padded_end - padded_start + 1
        if body_size > target_span * TRIM_THRESHOLD_FACTOR:
            parsed = parse_hunk_header(header_line)
            if parsed:
                old_s, _, new_s, _ = parsed
                header_line, body = trim_hunk_to_range(
                    header_line, body, old_s, new_s,
                    padded_start, padded_end,
                )

        result_lines.append(header_line)
        result_lines.extend(body)

    return "\n".join(result_lines)


def main():
    if len(sys.argv) < 4:
        usage()

    html_file = sys.argv[1]
    worktree_path = sys.argv[2]
    merge_base = sys.argv[3]

    # Determine input mode
    id_file_pairs: list[tuple[str, str, int | None, int | None]] = []

    if len(sys.argv) >= 5 and sys.argv[4] == "--pairs-file":
        if len(sys.argv) < 6:
            print("Error: --pairs-file requires a file path argument", file=sys.stderr)
            sys.exit(1)
        id_file_pairs = parse_pairs_file(sys.argv[5])
    elif len(sys.argv) >= 5:
        # Legacy CLI-arg mode: id:file pairs, no line range support
        for pair in sys.argv[4:]:
            sep_idx = pair.index(":")
            diff_id = pair[:sep_idx]
            file_path = pair[sep_idx + 1:]
            id_file_pairs.append((diff_id, file_path, None, None))
    else:
        usage()

    # Cache full diffs by file path
    diff_cache: dict[str, str] = {}
    for _, file_path, _, _ in id_file_pairs:
        if file_path not in diff_cache:
            diff_cache[file_path] = get_diff(worktree_path, merge_base, file_path)

    # Read the HTML file
    with open(html_file, "r", encoding="utf-8") as f:
        html = f.read()

    # Replace each placeholder tag
    for diff_id, file_path, start_line, end_line in id_file_pairs:
        raw_diff = diff_cache[file_path]

        # Filter to relevant hunks when line range is present
        if start_line is not None and end_line is not None:
            diff_to_inject = filter_diff_hunks(raw_diff, start_line, end_line)
        else:
            diff_to_inject = raw_diff

        escaped_diff = escape_script_close(diff_to_inject)

        # Match: <script type="application/diff" data-for="ID"></script>
        pattern = (
            r'(<script\s+type="application/diff"\s+data-for="'
            + re.escape(diff_id)
            + r'">)\s*</script>'
        )
        replacement = r'\1' + "\n" + escaped_diff + "</script>"
        html, count = re.subn(pattern, replacement, html, count=1)

        if count == 0:
            print(f"Warning: no placeholder found for diff_id={diff_id!r}", file=sys.stderr)
        else:
            range_info = f" lines {start_line}-{end_line}" if start_line is not None else ""
            print(f"Injected diff for {diff_id} ({file_path}{range_info}): "
                  f"{len(diff_to_inject)} bytes (full: {len(raw_diff)} bytes)")

    # Write back
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done. Updated {html_file}")


if __name__ == "__main__":
    main()
