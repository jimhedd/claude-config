#!/usr/bin/env python3
"""Assemble a PR review HTML report from a body fragment and the static template.

Usage:
    python3 assemble-report.py <body_file> <output_file> --title "PR Review: #42 - Fix bug"

The template is located relative to this script at ../templates/pr-review.html.
The body file is deleted on success; preserved on failure for debugging.
"""

import argparse
import html
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble PR review HTML report")
    parser.add_argument("body_file", help="Path to the HTML body fragment file")
    parser.add_argument("output_file", help="Path to write the assembled HTML")
    parser.add_argument("--title", required=True, help="Page title (will be HTML-escaped)")
    args = parser.parse_args()

    # Locate template relative to this script
    script_dir = Path(__file__).resolve().parent
    template_path = script_dir.parent / "templates" / "pr-review.html"

    if not template_path.is_file():
        print(f"ERROR: Template not found at {template_path}", file=sys.stderr)
        return 1

    # Read template
    template = template_path.read_text(encoding="utf-8")

    # Validate template placeholders — each must appear exactly once
    for placeholder in ("{{TITLE}}", "{{BODY}}"):
        count = template.count(placeholder)
        if count != 1:
            print(
                f"ERROR: Expected exactly 1 occurrence of {placeholder} in template, found {count}",
                file=sys.stderr,
            )
            return 1

    # Read body fragment
    body_path = Path(args.body_file)
    if not body_path.is_file():
        print(f"ERROR: Body file not found at {body_path}", file=sys.stderr)
        return 1

    body_content = body_path.read_text(encoding="utf-8")

    # Substitute template placeholders
    assembled = template.replace("{{TITLE}}", html.escape(args.title))
    assembled = assembled.replace("{{BODY}}", body_content)

    # Replace timestamp placeholders injected by the body fragment
    now = datetime.now(timezone.utc)
    assembled = assembled.replace("{{GENERATED_UTC}}", now.strftime("%Y-%m-%d %H:%M UTC"))
    assembled = assembled.replace("{{GENERATED_ISO}}", now.strftime("%Y-%m-%dT%H:%M:%SZ"))

    # Write output
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(assembled, encoding="utf-8")

    # Delete body file on success
    body_path.unlink()

    return 0


if __name__ == "__main__":
    sys.exit(main())
