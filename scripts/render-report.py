#!/usr/bin/env python3
"""Render a PR review HTML body fragment and pairs TSV from structured JSON.

Replaces the html-report-writer LLM agent with deterministic data transformation.

Usage:
    python3 render-report.py <json_file> <body_output> <pairs_output>

The JSON input schema is documented in review-github-pr.md Step 8a.
The body output is an HTML fragment for assemble-report.py.
The pairs output is a TSV file for inject-diff.py --pairs-file.
"""

from __future__ import annotations

import html as html_mod
import json
import os
import re
import sys
from typing import Any


# --- Utility functions ---


def esc(text: str) -> str:
    """HTML-escape text."""
    return html_mod.escape(str(text), quote=True)


def prose_to_html(text: str) -> str:
    """Convert markdown-ish prose to HTML.

    Handles:
    - Fenced code blocks (```...```) -> <pre><code>...</code></pre>
    - Inline backtick `code` -> <code>code</code>
    - HTML-escapes everything else
    """
    if not text:
        return ""

    parts: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        # Check for fenced code block
        if lines[i].strip().startswith("```"):
            # Extract language hint (unused but consumed)
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # skip closing ```
            code_text = esc("\n".join(code_lines))
            parts.append(f"<pre><code>{code_text}</code></pre>")
        else:
            # Process inline backticks in this line
            parts.append(_inline_backticks(esc(lines[i])))
            i += 1

    return "\n".join(parts)


def _inline_backticks(escaped_line: str) -> str:
    """Convert backtick `code` spans in already-escaped HTML text."""
    # Match `...` but not empty ``
    return re.sub(
        r"`([^`]+)`",
        r"<code>\1</code>",
        escaped_line,
    )


def file_display(path: str, line_range: str | None) -> tuple[str, str]:
    """Return (short_display, full_display) for a file path + optional line range.

    short_display: basename:range (or just basename)
    full_display: full/path:range (or just full/path)
    """
    basename = os.path.basename(path)
    if line_range:
        return (f"{basename}:{line_range}", f"{path}:{line_range}")
    return (basename, path)


# --- Validation ---


VALID_TIERS = {"p0", "p1", "p2", "nitpick"}
VALID_VERDICTS = {"APPROVE", "REQUEST_CHANGES"}
REQUIRED_ISSUE_FIELDS = {"id", "tier", "title", "reviewer", "file", "problem", "suggestion"}

_LINE_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
_SINGLE_LINE_RE = re.compile(r"^(\d+)$")


def validate_input(data: dict[str, Any]) -> list[str]:
    """Validate the input JSON schema. Returns list of error messages (empty = valid).

    Also normalizes single-line line_range values to N-N format in-place.
    """
    errors: list[str] = []

    # Top-level keys
    if "pr" not in data:
        errors.append("Missing required key: pr")
    if "verdicts" not in data:
        errors.append("Missing required key: verdicts")
    if "issues" not in data:
        errors.append("Missing required key: issues")

    if errors:
        return errors

    # PR fields
    REQUIRED_PR_FIELDS = {"number", "title", "base_ref", "head_ref", "base_sha", "head_sha",
                          "additions", "deletions", "changed_files"}
    pr = data["pr"]
    for field in REQUIRED_PR_FIELDS:
        if field not in pr or pr[field] is None:
            errors.append(f"pr: missing required field '{field}'")

    # Verdicts
    verdicts = data["verdicts"]
    for key in ("bug", "arch", "quality", "tests", "overall"):
        val = verdicts.get(key)
        if val not in VALID_VERDICTS:
            errors.append(f"verdicts.{key} must be APPROVE or REQUEST_CHANGES, got: {val!r}")

    # Issues
    issues = data["issues"]
    seen_ids: set[str] = set()
    for i, issue in enumerate(issues):
        # Required fields
        for field in REQUIRED_ISSUE_FIELDS:
            if field not in issue or issue[field] is None:
                errors.append(f"issues[{i}]: missing required field '{field}'")

        # ID uniqueness
        issue_id = issue.get("id", "")
        if issue_id in seen_ids:
            errors.append(f"issues[{i}]: duplicate id '{issue_id}'")
        seen_ids.add(issue_id)

        # Tier validation
        tier = issue.get("tier", "")
        if tier not in VALID_TIERS:
            errors.append(f"issues[{i}]: tier must be one of {VALID_TIERS}, got: {tier!r}")

        # Line range validation and normalization
        lr = issue.get("line_range")
        if lr is not None:
            m = _LINE_RANGE_RE.match(str(lr))
            if m:
                start, end = int(m.group(1)), int(m.group(2))
                if start > end:
                    errors.append(f"issues[{i}]: line_range start ({start}) > end ({end})")
            else:
                sm = _SINGLE_LINE_RE.match(str(lr))
                if sm:
                    # Normalize single line to N-N
                    issue["line_range"] = f"{lr}-{lr}"
                else:
                    errors.append(f"issues[{i}]: invalid line_range '{lr}', expected 'N-N' or 'N'")

    return errors


# --- Render functions ---


TIER_LABELS = {
    "p0": "P0 — Must Fix",
    "p1": "P1 — Should Fix",
    "p2": "P2 — Consider Fixing",
    "nitpick": "Nitpick",
}

TIER_ORDER = ["p0", "p1", "p2", "nitpick"]


def render_header(pr: dict[str, Any]) -> str:
    """Render the <header> section."""
    number = esc(str(pr["number"]))
    title = esc(pr["title"])
    base_ref = esc(pr["base_ref"])
    head_ref = esc(pr["head_ref"])
    base_sha = esc(str(pr["base_sha"])[:8])
    head_sha = esc(str(pr["head_sha"])[:8])
    additions = esc(str(pr["additions"]))
    deletions = esc(str(pr["deletions"]))
    changed_files = pr["changed_files"]
    changed_files_esc = esc(str(changed_files))

    return f"""<header>
  <h1>PR #{number} &mdash; {title}</h1>
  <p class="meta">{base_ref} ({base_sha}) &rarr; {head_ref} ({head_sha}) &middot; {changed_files_esc} file{"s" if changed_files != 1 else ""} &middot; <span class="additions">+{additions}</span> / <span class="deletions">-{deletions}</span></p>
  <p class="meta">Generated: {{{{GENERATED_UTC}}}} <span class="time-ago" data-generated="{{{{GENERATED_ISO}}}}"></span></p>
</header>"""


def render_summary_bar(verdicts: dict[str, str], tier_counts: dict[str, int]) -> str:
    """Render the summary bar with badges and chips."""
    parts: list[str] = ['<div class="summary">']

    # Reviewer badges
    parts.append('  <div class="summary-group">')
    for key in ("bug", "arch", "quality", "tests"):
        verdict = verdicts[key]
        css = "badge-approve" if verdict == "APPROVE" else "badge-request-changes"
        parts.append(f'    <span class="badge {css}">{key}={verdict}</span>')
    parts.append("  </div>")

    # Divider
    parts.append('  <span class="divider"></span>')

    # Overall badge
    overall = verdicts["overall"]
    css = "badge-approve" if overall == "APPROVE" else "badge-request-changes"
    parts.append(f'  <span class="badge badge-overall {css}">Overall: {overall}</span>')

    # Divider
    parts.append('  <span class="divider"></span>')

    # Count chips
    parts.append('  <div class="summary-group">')
    for tier in TIER_ORDER:
        count = tier_counts.get(tier, 0)
        chip_class = f"chip-{tier}"
        zero_class = " chip-zero" if count == 0 else ""
        label = tier.upper() if tier != "nitpick" else "nitpick"
        parts.append(f'    <span class="chip {chip_class}{zero_class}">{count} {label}</span>')
    parts.append("  </div>")

    parts.append("</div>")
    return "\n".join(parts)


def render_guidelines(guidelines: dict[str, Any] | None) -> str:
    """Render the guidelines context section."""
    if guidelines is None:
        guidelines = {}

    expected_files = guidelines.get("expected_files", [])
    expected_directives = guidelines.get("expected_directives", [])
    pr_added_files = guidelines.get("pr_added_files", [])
    reviewers = guidelines.get("reviewers", {})
    warnings = guidelines.get("warnings", [])

    parts: list[str] = ['<section class="guidelines">']
    parts.append("  <details>")

    # Status badge
    has_warnings = len(warnings) > 0
    status_class = "guidelines-warn" if has_warnings else "guidelines-ok"
    reviewer_count = len(reviewers)
    matched_count = sum(1 for r in reviewers.values() if r.get("matched", False))
    if not expected_files:
        status_text = "No CLAUDE.md files"
    else:
        status_text = f"{matched_count}/{reviewer_count} reviewers matched"

    parts.append(f'    <summary><strong>Guidelines Context</strong> <span class="guidelines-status {status_class}">{esc(status_text)}</span></summary>')

    if not expected_files:
        parts.append("    <p>No CLAUDE.md files found in ancestor directories.</p>")
    else:
        # Expected files
        parts.append('    <div class="guidelines-expected">')
        parts.append("      <h4>Expected CLAUDE.md Files</h4>")
        parts.append("      <ul>")

        # Group directives by parent path
        directives_by_parent: dict[str, list[dict[str, Any]]] = {}
        for d in expected_directives:
            parent = d.get("parent_path", "")
            directives_by_parent.setdefault(parent, []).append(d)

        for f in expected_files:
            child_directives = directives_by_parent.get(f, [])
            if child_directives:
                parts.append(f"        <li><code>{esc(f)}</code>")
                parts.append("          <ul>")
                for d in child_directives:
                    dt = esc(d.get("directive_text", ""))
                    rp = esc(d.get("resolved_path", ""))
                    parts.append(f"            <li><code>{dt}</code> &rarr; <code>{rp}</code></li>")
                parts.append("          </ul>")
                parts.append("        </li>")
            else:
                parts.append(f"        <li><code>{esc(f)}</code></li>")
        parts.append("      </ul>")
        parts.append("    </div>")

        # Reviewer reports table
        parts.append('    <div class="guidelines-reviewers">')
        parts.append("      <h4>Reviewer Reports</h4>")
        parts.append('      <table class="guidelines-table">')
        parts.append("        <tr><th>Reviewer</th><th>Files</th><th>Directives</th><th>Status</th></tr>")
        for rkey in ("bug", "arch", "quality", "tests"):
            rdata = reviewers.get(rkey, {})
            fc = rdata.get("files_count", 0)
            dc = rdata.get("directives_count", 0)
            matched = rdata.get("matched", False)
            if matched:
                parts.append(f'        <tr><td>{rkey}</td><td>{fc}</td><td>{dc}</td><td class="guidelines-ok">&#x2713; matched</td></tr>')
            else:
                parts.append(f'        <tr><td>{rkey}</td><td>{fc}</td><td>{dc}</td><td class="guidelines-warn">&#x26A0; mismatch</td></tr>')
        parts.append("      </table>")
        parts.append("    </div>")

    # Warnings
    if warnings:
        parts.append('    <div class="guidelines-warnings">')
        parts.append("      <h4>Warnings</h4>")
        parts.append("      <ul>")
        for w in warnings:
            parts.append(f"        <li>&#x26A0; {esc(w)}</li>")
        parts.append("      </ul>")
        parts.append("    </div>")

    # PR-added notice
    if pr_added_files:
        parts.append('    <div class="guidelines-notice">')
        parts.append("      <strong>Note:</strong> This PR adds CLAUDE.md files that were not used for review")
        parts.append("      (trust rule: only merge-base content is trusted):")
        parts.append("      <ul>")
        for f in pr_added_files:
            parts.append(f"        <li><code>{esc(f)}</code></li>")
        parts.append("      </ul>")
        parts.append("    </div>")

    parts.append("  </details>")
    parts.append("</section>")
    return "\n".join(parts)


def render_toc(issues_by_tier: dict[str, list[dict[str, Any]]]) -> str:
    """Render the table of contents navigation."""
    total = sum(len(v) for v in issues_by_tier.values())
    parts: list[str] = ['<nav class="toc">']
    parts.append("  <details open>")
    parts.append(f'    <summary><strong>Issues ({total})</strong> <button type="button" class="copy-all-md" title="Copy all issues as Markdown">Copy All</button></summary>')

    for tier in TIER_ORDER:
        issues = issues_by_tier.get(tier, [])
        if not issues:
            continue
        label = TIER_LABELS[tier]
        parts.append(f'    <div class="toc-group">')
        parts.append(f'      <span class="toc-tier toc-{tier}">{label} ({len(issues)})</span>')
        parts.append("      <ul>")
        for issue in issues:
            iid = esc(issue["id"])
            title = esc(issue["title"])
            parts.append(f'        <li><a href="#{iid}" class="toc-{tier}">[{iid}] {title}</a></li>')
        parts.append("      </ul>")
        parts.append("    </div>")

    parts.append("  </details>")
    parts.append("</nav>")
    return "\n".join(parts)


def render_toggle_bar() -> str:
    """Render the static 4-button toggle bar."""
    return """<div class="toggle-bar">
  <button type="button" class="toggle-btn toggle-tiers" data-action="expand">Expand All Sections</button>
  <button type="button" class="toggle-btn toggle-tiers" data-action="collapse">Collapse All Sections</button>
  <button type="button" class="toggle-btn toggle-diffs" data-action="expand">Expand All Diffs</button>
  <button type="button" class="toggle-btn toggle-diffs" data-action="collapse">Collapse All Diffs</button>
</div>"""


def render_issue_card(issue: dict[str, Any]) -> str:
    """Render a single issue card."""
    iid = issue["id"]
    tier = issue["tier"]
    title = esc(issue["title"])
    reviewer = esc(issue.get("reviewer", ""))
    severity = esc(issue.get("severity", ""))
    category = esc(issue.get("category", ""))
    file_path = issue["file"]
    line_range = issue.get("line_range")
    problem = issue["problem"]
    suggestion = issue["suggestion"]

    short_file, full_file = file_display(file_path, line_range)

    # Diff open attribute for P0/P1
    diff_open = " open" if tier in ("p0", "p1") else ""

    # Severity/category tag
    sev_cat_tag = f"{severity} / {category}" if severity and category else severity or category

    # Diff summary: basename only
    diff_basename = os.path.basename(file_path)

    # Build markdown copy block
    tier_label = TIER_LABELS.get(tier, tier)
    md_block = _build_markdown_block(iid, issue["title"], tier_label, file_path, line_range, issue.get("reviewer", ""), problem, suggestion)

    parts: list[str] = []
    parts.append(f'<div class="card" id="{esc(iid)}">')
    parts.append(f'  <div class="card-header">')
    parts.append(f'    <span class="id">[{esc(iid)}]</span>')
    parts.append(f'    <span class="title">{title}</span>')
    parts.append(f'    <span class="tag">{reviewer}</span>')
    parts.append(f'    <span class="tag">{sev_cat_tag}</span>')
    parts.append(f'    <button class="copy-md" data-issue="{esc(iid)}" title="Copy as Markdown">Copy</button>')
    parts.append(f'  </div>')
    parts.append(f'  <div class="card-body">')
    parts.append(f'    <p><span class="label">File:</span> <span class="file-path" title="{esc(full_file)}">{esc(short_file)}</span></p>')
    parts.append(f'    <p><span class="label">Problem:</span> {prose_to_html(problem)}</p>')
    parts.append(f'    <div class="fix"><span class="label">Fix:</span> {prose_to_html(suggestion)}</div>')
    parts.append(f'    <details class="diff-container"{diff_open}>')
    parts.append(f'      <summary title="{esc(file_path)}">Diff: {esc(diff_basename)}</summary>')
    parts.append(f'      <div class="diff-viewer" data-diff-id="{esc(iid)}"></div>')
    parts.append(f'      <script type="application/diff" data-for="{esc(iid)}"></script>')
    parts.append(f'    </details>')
    parts.append(f'  </div>')
    # Markdown copy block - escape </script in content
    escaped_md = md_block.replace("</script", "<\\/script")
    parts.append(f'  <script type="text/markdown" data-for="{esc(iid)}">')
    parts.append(escaped_md)
    parts.append(f'  </script>')
    parts.append(f'</div>')
    return "\n".join(parts)


def _build_markdown_block(
    iid: str, title: str, tier_label: str,
    file_path: str, line_range: str | None,
    reviewer: str, problem: str, suggestion: str,
) -> str:
    """Build the markdown text for copy-as-markdown."""
    file_ref = f"`{file_path}:{line_range}`" if line_range else f"`{file_path}`"
    return f"""## [{iid}] {title}

**Severity:** {tier_label}
**File:** {file_ref}
**Reviewer:** {reviewer}

### Problem
{problem}

### Suggested Fix
{suggestion}"""


def render_tier_section(tier: str, issues: list[dict[str, Any]]) -> str:
    """Render a tier section with all its issue cards."""
    label = TIER_LABELS[tier]
    count = len(issues)

    parts: list[str] = []
    parts.append(f'<details open class="tier-{tier}">')
    parts.append(f"  <summary><h2>{label} ({count})</h2></summary>")
    for issue in issues:
        parts.append(render_issue_card(issue))
    parts.append("</details>")
    parts.append(f'<p class="back-to-top"><a href="#">Back to top</a></p>')
    return "\n".join(parts)


# --- Top-level generators ---


def generate_body(data: dict[str, Any]) -> str:
    """Generate the complete HTML body fragment."""
    pr = data["pr"]
    verdicts = data["verdicts"]
    issues = data.get("issues", [])
    guidelines = data.get("guidelines")

    # Group issues by tier
    issues_by_tier: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        tier = issue["tier"]
        issues_by_tier.setdefault(tier, []).append(issue)

    tier_counts = {t: len(issues_by_tier.get(t, [])) for t in TIER_ORDER}

    parts: list[str] = []

    # Header
    parts.append(render_header(pr))

    # Summary bar
    parts.append(render_summary_bar(verdicts, tier_counts))

    # Guidelines (always included)
    parts.append(render_guidelines(guidelines))

    # Zero-issues case: omit TOC, toggle bar, and main
    has_issues = len(issues) > 0
    if has_issues:
        parts.append(render_toc(issues_by_tier))
        parts.append(render_toggle_bar())
        parts.append("<main>")
        for tier in TIER_ORDER:
            tier_issues = issues_by_tier.get(tier, [])
            if tier_issues:
                parts.append(render_tier_section(tier, tier_issues))
        parts.append("</main>")

    return "\n".join(parts)


def generate_pairs_tsv(issues: list[dict[str, Any]]) -> str:
    """Generate TSV content matching inject-diff.py's parse_pairs_file() format.

    Format: id<TAB>file[<TAB>start-end]
    """
    lines: list[str] = []
    for issue in issues:
        iid = issue["id"]
        file_path = issue["file"]
        line_range = issue.get("line_range")
        if line_range:
            lines.append(f"{iid}\t{file_path}\t{line_range}")
        else:
            lines.append(f"{iid}\t{file_path}")
    return "\n".join(lines) + ("\n" if lines else "")


# --- Main ---


def main() -> int:
    if len(sys.argv) != 4:
        print(
            f"Usage: {sys.argv[0]} <json_file> <body_output> <pairs_output>",
            file=sys.stderr,
        )
        return 1

    json_path = sys.argv[1]
    body_path = sys.argv[2]
    pairs_path = sys.argv[3]

    # Read and parse JSON
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Error reading JSON: {e}", file=sys.stderr)
        return 1

    # Validate
    errors = validate_input(data)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    # Generate outputs
    body_html = generate_body(data)
    pairs_tsv = generate_pairs_tsv(data.get("issues", []))

    # Write outputs — clean up body file if pairs write fails
    try:
        with open(body_path, "w", encoding="utf-8") as f:
            f.write(body_html)
    except OSError as e:
        print(f"Error writing body: {e}", file=sys.stderr)
        return 1

    try:
        with open(pairs_path, "w", encoding="utf-8") as f:
            f.write(pairs_tsv)
    except OSError as e:
        # Clean up orphaned body file
        try:
            os.unlink(body_path)
        except OSError:
            pass
        print(f"Error writing pairs: {e}", file=sys.stderr)
        return 1

    print(f"Rendered body: {body_path}")
    print(f"Rendered pairs: {pairs_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
