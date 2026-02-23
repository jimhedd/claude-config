#!/usr/bin/env python3
"""Resolve CLAUDE.md files and @ directives for a git repository.

Replaces LLM-driven CLAUDE.md probing with a single script invocation.
Handles discovery, reading, @ directive resolution, budget management,
and output formatting.
"""

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class Directive:
    parent_path: str
    directive: str
    resolved_path: str
    exists_at_merge_base: bool
    status: str  # resolved, truncated, not-found, cycle-skipped, budget-dropped
    depth: int


@dataclass
class Guideline:
    path: str
    exists_at_merge_base: bool


@dataclass
class ResolveContext:
    git_dir: str
    merge_base: Optional[str]
    working_tree: bool
    depth_limit: int
    budget: int
    budget_remaining: int = 0


def run_git(git_dir: str, *args: str) -> subprocess.CompletedProcess:
    """Run a git command in the specified directory."""
    cmd = ["git", "-C", git_dir] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def file_exists_at_ref(git_dir: str, ref: str, path: str) -> bool:
    """Check if a file exists at the given ref using cat-file -e."""
    result = run_git(git_dir, "cat-file", "-e", f"{ref}:{path}")
    return result.returncode == 0


def read_file_at_ref(git_dir: str, ref: str, path: str) -> Optional[str]:
    """Read file content at the given ref using git show."""
    result = run_git(git_dir, "show", f"{ref}:{path}")
    if result.returncode == 0:
        return result.stdout
    return None


def read_file_from_disk(git_dir: str, path: str) -> Optional[str]:
    """Read file content from disk (working tree mode)."""
    full_path = os.path.join(git_dir, path)
    try:
        with open(full_path, "r") as f:
            return f.read()
    except OSError:
        return None


def file_exists_on_disk(git_dir: str, path: str) -> bool:
    """Check if a file exists on disk (working tree mode)."""
    full_path = os.path.join(git_dir, path)
    return os.path.isfile(full_path)


def compute_ancestor_dirs(changed_files: list[str]) -> list[str]:
    """Extract ancestor directories from changed file paths.

    Always includes root. Returns deduplicated set.
    """
    dirs = set()
    dirs.add("")  # root is always included
    for filepath in changed_files:
        parts = filepath.split("/")
        for i in range(1, len(parts)):
            dirs.add("/".join(parts[:i]))
    return list(dirs)


def sort_ancestor_dirs(dirs: list[str]) -> list[str]:
    """Sort dirs deepest-first, then lexicographic, root last."""
    def sort_key(d):
        if d == "":
            return (float("inf"), "")
        depth = d.count("/")
        return (-depth, d)
    return sorted(dirs, key=sort_key)


def format_ancestor_dirs_list(sorted_dirs: list[str]) -> str:
    """Format dirs for display: comma-separated, (root) for empty string."""
    parts = []
    for d in sorted_dirs:
        if d == "":
            parts.append("(root)")
        else:
            parts.append(d)
    return ", ".join(parts)


def is_inside_fenced_code_block(lines: list[str], target_line_idx: int) -> bool:
    """Check if a line is inside a fenced code block.

    Implements CommonMark fenced code block detection:
    - Opens with 3+ consecutive backticks or tildes (optionally preceded by up to 3 spaces)
    - Closes with >= same count of same fence char, no other non-whitespace
    - Nested fences (longer sequence inside shorter) do not close outer
    """
    in_fence = False
    fence_char = None
    fence_count = 0

    for i in range(target_line_idx):
        line = lines[i]
        if not in_fence:
            m = re.match(r"^( {0,3})((`{3,})|(~{3,}))", line)
            if m:
                if m.group(3):
                    # Backtick fence: info string must not contain backticks
                    remainder = line[m.end():]
                    if "`" not in remainder:
                        in_fence = True
                        fence_char = "`"
                        fence_count = len(m.group(3))
                else:
                    in_fence = True
                    fence_char = "~"
                    fence_count = len(m.group(4))
        else:
            # Check if this line closes the fence
            if fence_char == "`":
                close_match = re.match(r"^( {0,3})(`{3,})$", line.rstrip())
                if close_match and len(close_match.group(2)) >= fence_count:
                    in_fence = False
                    fence_char = None
                    fence_count = 0
            elif fence_char == "~":
                close_match = re.match(r"^( {0,3})(~{3,})$", line.rstrip())
                if close_match and len(close_match.group(2)) >= fence_count:
                    in_fence = False
                    fence_char = None
                    fence_count = 0

    return in_fence


def is_inside_inline_code_span(line: str, at_position: int) -> bool:
    """Check if a position is inside an inline code span.

    Per CommonMark: inline code spans are delimited by matched backtick sequences.
    A line with zero backticks never has inline code spans.
    """
    if "`" not in line:
        return False

    # Find all backtick-delimited code spans on this line
    i = 0
    spans = []  # list of (start, end) character positions inside code spans
    while i < len(line):
        if line[i] == "`":
            # Count opening backticks
            open_count = 0
            while i < len(line) and line[i] == "`":
                open_count += 1
                i += 1
            # Look for matching closing backticks
            content_start = i
            found_close = False
            while i < len(line):
                if line[i] == "`":
                    close_start = i
                    close_count = 0
                    while i < len(line) and line[i] == "`":
                        close_count += 1
                        i += 1
                    if close_count == open_count:
                        # Found matching close
                        spans.append((content_start, close_start))
                        found_close = True
                        break
                    # Not matching, continue searching
                else:
                    i += 1
            if not found_close:
                # No matching close found, these backticks are literal.
                # Reset i so subsequent backtick runs can be tried as openers.
                i = content_start
        else:
            i += 1

    # Check if at_position falls within any code span
    for start, end in spans:
        if start <= at_position < end:
            return True
    return False


# Regex for safe path characters
SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/~-]+$")
DOTDOT_COMPONENT_RE = re.compile(r"(^|/)\.\.(/|$)")


def is_directive_line(line: str, lines: list[str], line_idx: int) -> Optional[str]:
    """Check if a line is an @ directive. Returns the path or None.

    Conditions:
    (a) Trimmed content matches @<path> with safe path chars, no .. components
    (b) @ is first non-whitespace character
    (c) Not inside fenced code block
    (d) Not inside inline code span
    """
    stripped = line.strip()
    if not stripped:
        return None

    # (b) @ must be first non-whitespace
    if not stripped.startswith("@"):
        return None

    # Extract path part
    path = stripped[1:]
    if not path:
        return None

    # (a) Safe path characters only
    if not SAFE_PATH_RE.match(path):
        return None

    # (a) No .. path components
    if DOTDOT_COMPONENT_RE.search(path):
        return None

    # Reject absolute paths
    if path.startswith("/"):
        return None

    # (c) Not inside fenced code block
    if is_inside_fenced_code_block(lines, line_idx):
        return None

    # (d) Not inside inline code span
    at_pos = line.index("@")
    if is_inside_inline_code_span(line, at_pos):
        return None

    return path


def resolve_path(directive_path: str, parent_dir: str) -> str:
    """Resolve a directive path relative to the CLAUDE.md's directory."""
    if parent_dir:
        return posixpath.normpath(posixpath.join(parent_dir, directive_path))
    return posixpath.normpath(directive_path)


def path_escapes_root(resolved_path: str) -> bool:
    """Check if a resolved path escapes the repository root."""
    if resolved_path.startswith("/"):
        return True
    if resolved_path.startswith("..") or "/../" in resolved_path:
        return True
    normalized = posixpath.normpath(resolved_path)
    if normalized.startswith(".."):
        return True
    return False


def get_parent_dir(claude_md_path: str) -> str:
    """Get the directory containing a CLAUDE.md file."""
    # For "dir/CLAUDE.md" -> "dir"
    # For "dir/.claude/CLAUDE.md" -> "dir"
    # For "CLAUDE.md" -> ""
    # For ".claude/CLAUDE.md" -> ""
    parts = claude_md_path.split("/")
    if len(parts) == 1:
        return ""
    if parts[-2] == ".claude":
        return "/".join(parts[:-2]) if len(parts) > 2 else ""
    return "/".join(parts[:-1])


def resolve_directives_in_content(
    content: str,
    parent_dir: str,
    parent_path: str,
    ctx: ResolveContext,
    current_depth: int,
    chain_paths: set,
) -> tuple[str, list[Directive]]:
    """Resolve @ directives in content, returning modified content and directive records.

    Implements bounded recursion with cycle detection and budget management.
    """
    if current_depth > ctx.depth_limit:
        return content, []

    lines = content.split("\n")
    result_lines = []
    directives_found = []

    for i, line in enumerate(lines):
        directive_path = is_directive_line(line, lines, i)
        if directive_path is None:
            result_lines.append(line)
            continue

        resolved = resolve_path(directive_path, parent_dir)

        # Path safety check
        if path_escapes_root(resolved):
            # Silently skip - drop directive line
            continue

        # Cycle detection
        if resolved in chain_paths:
            directives_found.append(Directive(
                parent_path=parent_path,
                directive=directive_path,
                resolved_path=resolved,
                exists_at_merge_base=True,
                status="cycle-skipped",
                depth=current_depth,
            ))
            continue

        # Check existence
        if ctx.working_tree:
            exists = file_exists_on_disk(ctx.git_dir, resolved)
        else:
            exists = file_exists_at_ref(ctx.git_dir, ctx.merge_base, resolved)

        if not exists:
            directives_found.append(Directive(
                parent_path=parent_path,
                directive=directive_path,
                resolved_path=resolved,
                exists_at_merge_base=False,
                status="not-found",
                depth=current_depth,
            ))
            # Drop the directive line (don't add to result_lines)
            continue

        # Budget check
        if ctx.budget_remaining < 12:
            directives_found.append(Directive(
                parent_path=parent_path,
                directive=directive_path,
                resolved_path=resolved,
                exists_at_merge_base=True,
                status="budget-dropped",
                depth=current_depth,
            ))
            continue

        # Read content
        if ctx.working_tree:
            ref_content = read_file_from_disk(ctx.git_dir, resolved)
        else:
            ref_content = read_file_at_ref(ctx.git_dir, ctx.merge_base, resolved)

        if ref_content is None:
            directives_found.append(Directive(
                parent_path=parent_path,
                directive=directive_path,
                resolved_path=resolved,
                exists_at_merge_base=True,
                status="not-found",
                depth=current_depth,
            ))
            continue

        # Apply budget
        status = "resolved"
        if len(ref_content) > ctx.budget_remaining:
            # Truncate: content + "[truncated]" (11 chars) must fit
            available = ctx.budget_remaining - 11
            if available <= 0:
                directives_found.append(Directive(
                    parent_path=parent_path,
                    directive=directive_path,
                    resolved_path=resolved,
                    exists_at_merge_base=True,
                    status="budget-dropped",
                    depth=current_depth,
                ))
                continue
            ref_content = ref_content[:available] + "[truncated]"
            status = "truncated"
            ctx.budget_remaining = 0
        else:
            ctx.budget_remaining -= len(ref_content)

        # Record directive
        directives_found.append(Directive(
            parent_path=parent_path,
            directive=directive_path,
            resolved_path=resolved,
            exists_at_merge_base=True,
            status=status,
            depth=current_depth,
        ))

        # Recurse into fetched content
        new_chain = chain_paths | {resolved}
        resolved_dir = posixpath.dirname(resolved) if "/" in resolved else ""
        sub_content, sub_directives = resolve_directives_in_content(
            ref_content, resolved_dir, resolved, ctx,
            current_depth + 1, new_chain,
        )
        directives_found.extend(sub_directives)
        result_lines.append(sub_content)

    return "\n".join(result_lines), directives_found


def probe_claude_md_paths(
    git_dir: str, ref: Optional[str], ancestor_dir: str, working_tree: bool
) -> list[str]:
    """Probe for CLAUDE.md and .claude/CLAUDE.md in a directory at a ref.

    Returns list of existing paths.
    """
    found = []
    if ancestor_dir == "":
        candidates = ["CLAUDE.md", ".claude/CLAUDE.md"]
    else:
        candidates = [
            f"{ancestor_dir}/CLAUDE.md",
            f"{ancestor_dir}/.claude/CLAUDE.md",
        ]

    for path in candidates:
        if working_tree:
            if file_exists_on_disk(git_dir, path):
                found.append(path)
        else:
            if file_exists_at_ref(git_dir, ref, path):
                found.append(path)

    return found


def main():
    parser = argparse.ArgumentParser(
        description="Resolve CLAUDE.md files and @ directives for a git repository."
    )
    parser.add_argument("--git-dir", required=True, help="Directory for git -C commands")
    parser.add_argument("--merge-base", help="Trusted commit for reading content")
    parser.add_argument("--working-tree", action="store_true",
                        help="Read from working tree instead of merge-base")
    parser.add_argument("--ref-range", help="e.g. abc123..HEAD for computing changed files")
    parser.add_argument("--depth", type=int, default=5, help="Max @ recursion depth")
    parser.add_argument("--budget", type=int, default=8000, help="Char budget")
    parser.add_argument("--check-head", action="store_true",
                        help="Also probe HEAD for pr_added_guidelines")

    args = parser.parse_args()

    # Validate mode
    if args.merge_base and args.working_tree:
        print("Error: --merge-base and --working-tree are mutually exclusive", file=sys.stderr)
        sys.exit(1)
    if not args.merge_base and not args.working_tree:
        print("Error: one of --merge-base or --working-tree is required", file=sys.stderr)
        sys.exit(1)
    if args.check_head and not args.merge_base:
        print("Error: --check-head requires --merge-base", file=sys.stderr)
        sys.exit(1)

    git_dir = args.git_dir
    ref = args.merge_base if args.merge_base else None

    # Step 1: Compute ancestor directories from changed files
    changed_files = []
    if args.ref_range:
        result = run_git(git_dir, "diff", "--name-only", args.ref_range)
        if result.returncode != 0:
            print(f"Error: git diff --name-only failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        changed_files = [f for f in result.stdout.strip().split("\n") if f]

    ancestor_dirs = compute_ancestor_dirs(changed_files)
    sorted_dirs = sort_ancestor_dirs(ancestor_dirs)
    ancestor_dirs_list = format_ancestor_dirs_list(sorted_dirs)
    ancestor_set = set(ancestor_dirs)

    # Step 2: Per-directory CLAUDE.md probing
    expected_guidelines = []
    guideline_paths_set = set()

    for d in sorted_dirs:
        if args.working_tree:
            found = probe_claude_md_paths(git_dir, None, d, True)
        else:
            found = probe_claude_md_paths(git_dir, ref, d, False)
        for path in found:
            if path not in guideline_paths_set:
                expected_guidelines.append(Guideline(path=path, exists_at_merge_base=True))
                guideline_paths_set.add(path)

    # Step 3: Tree-wide verification (merge-base mode only, diagnostics)
    warnings = []
    if not args.working_tree and ref:
        ls_result = run_git(git_dir, "ls-tree", "-r", "--name-only", ref)
        if ls_result.returncode != 0:
            print(f"Error: git ls-tree failed: {ls_result.stderr}", file=sys.stderr)
            sys.exit(1)

        # Filter for CLAUDE.md paths
        claude_md_matches = []
        for line in ls_result.stdout.split("\n"):
            line = line.strip()
            if line and (line == "CLAUDE.md" or line.endswith("/CLAUDE.md")):
                claude_md_matches.append(line)

        # Check if any matches were missed by per-directory probe
        for match in claude_md_matches:
            ancestor = get_parent_dir(match)
            if ancestor not in ancestor_set:
                continue  # Outside ancestor set, ignore
            if match not in guideline_paths_set:
                warnings.append(f"tree-scan found {match} missed by per-directory probe")
                expected_guidelines.append(Guideline(path=match, exists_at_merge_base=True))
                guideline_paths_set.add(match)

    # Step 4: Read content and resolve @ directives
    ctx = ResolveContext(
        git_dir=git_dir,
        merge_base=ref,
        working_tree=args.working_tree,
        depth_limit=args.depth,
        budget=args.budget,
        budget_remaining=args.budget,
    )

    resolved_content_parts = []
    all_directives = []
    guidelines_loaded_lines = []
    source_label = "working-tree" if args.working_tree else "merge-base"

    for guideline in expected_guidelines:
        # Read content
        if args.working_tree:
            content = read_file_from_disk(git_dir, guideline.path)
        else:
            content = read_file_at_ref(git_dir, ref, guideline.path)

        if content is None:
            continue

        # Apply budget to the CLAUDE.md content itself
        if len(content) > ctx.budget_remaining:
            available = ctx.budget_remaining - 11
            if available <= 0:
                guidelines_loaded_lines.append(f"- {guideline.path} ({source_label}, budget-exhausted)")
                continue
            content = content[:available] + "[truncated]"
            ctx.budget_remaining = 0
        else:
            ctx.budget_remaining -= len(content)

        # Resolve @ directives in this content
        parent_dir = get_parent_dir(guideline.path)
        resolved_text, directives = resolve_directives_in_content(
            content, parent_dir, guideline.path, ctx, 1, set(),
        )

        resolved_content_parts.append(resolved_text)
        all_directives.extend(directives)

        # Build guidelines_loaded_section entry
        gl_line = f"- {guideline.path} ({source_label})"
        guidelines_loaded_lines.append(gl_line)

        # Add all directive sub-items with depth-based indentation
        for d in directives:
            indent = "  " * min(d.depth, 5)
            guidelines_loaded_lines.append(
                f"{indent}- @{d.directive} -> {d.resolved_path} ({d.status})"
            )

    if not guidelines_loaded_lines:
        guidelines_loaded_lines.append("None found.")

    # Step 5: HEAD probing for pr_added_guidelines
    pr_added_guidelines = []
    if args.check_head and ref:
        for d in sorted_dirs:
            head_paths = probe_claude_md_paths(git_dir, "HEAD", d, False)
            for path in head_paths:
                if path not in guideline_paths_set:
                    pr_added_guidelines.append(path)

    # Build output
    output = {
        "ancestor_dirs_list": ancestor_dirs_list,
        "expected_guidelines": [
            {"path": g.path, "exists_at_merge_base": g.exists_at_merge_base}
            for g in expected_guidelines
        ],
        "expected_directives": [
            {
                "parent_path": d.parent_path,
                "directive": d.directive,
                "resolved_path": d.resolved_path,
                "exists_at_merge_base": d.exists_at_merge_base,
                "status": d.status,
                "depth": d.depth,
            }
            for d in all_directives
        ],
        "pr_added_guidelines": pr_added_guidelines,
        "warnings": warnings,
        "guidelines_loaded_section": "\n".join(guidelines_loaded_lines),
        "resolved_content": "\n\n".join(resolved_content_parts),
    }

    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
