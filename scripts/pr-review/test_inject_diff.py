"""Tests for inject-diff.py — filter_diff_hunks function."""

import importlib.util
import os

import pytest

# Import the script as a module
spec = importlib.util.spec_from_file_location(
    "inject_diff",
    os.path.join(os.path.dirname(__file__), "inject-diff.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _make_new_file_diff(total_lines: int) -> str:
    """Build a synthetic new-file diff with `total_lines` added lines."""
    header = (
        "diff --git a/Foo.kt b/Foo.kt\n"
        "new file mode 100644\n"
        "index 0000000..abcdef1\n"
        "--- /dev/null\n"
        "+++ b/Foo.kt\n"
        f"@@ -0,0 +1,{total_lines} @@\n"
    )
    body = "".join(f"+line {n}\n" for n in range(1, total_lines + 1))
    return header + body


def _make_existing_file_diff(hunk_header: str, body_lines: list[str]) -> str:
    """Build a synthetic diff for an existing file with a single hunk."""
    header = (
        "diff --git a/Foo.kt b/Foo.kt\n"
        "index abcdef1..abcdef2 100644\n"
        "--- a/Foo.kt\n"
        "+++ b/Foo.kt\n"
    )
    return header + hunk_header + "\n" + "\n".join(body_lines)


class TestFilterDiffHunks:
    """Tests for filter_diff_hunks."""

    def test_new_file_trims_to_target_range(self):
        """New-file diff with 109 lines targeting lines 46-75 should be trimmed.

        With CONTEXT_PADDING=5, padded range is 41-80 (40 lines).
        For new files, factor=1, so 109 > 40*1 => trim applies.
        The trimmed output should cover approximately lines 41-80.
        """
        diff = _make_new_file_diff(109)
        result = mod.filter_diff_hunks(diff, 46, 75)
        result_lines = result.split("\n")

        # Should still have the file header
        assert "--- /dev/null" in result

        # Count body lines (lines starting with +)
        plus_lines = [l for l in result_lines if l.startswith("+") and not l.startswith("+++")]
        # Padded range 41-80 = 40 lines; trimmed output should be around that size
        assert len(plus_lines) < 109, "Should have trimmed the diff"
        assert len(plus_lines) >= 30, "Should still contain the target range"
        assert len(plus_lines) <= 50, "Should not be much larger than padded range"

        # Verify the target lines are present
        assert "+line 46\n" in result
        assert "+line 75\n" in result

    def test_regular_hunk_under_3x_threshold_not_trimmed(self):
        """A 50-line hunk with 20-line target (padded span ~30): 50 < 30*3=90, no trim."""
        body = [f"+line {n}" for n in range(10, 60)]  # 50 added lines at new-file line 10
        hunk_header = "@@ -10,0 +10,50 @@"
        diff = _make_existing_file_diff(hunk_header, body)

        result = mod.filter_diff_hunks(diff, 20, 39)  # 20-line target, padded: 15-44 => span=30
        plus_lines = [l for l in result.split("\n") if l.startswith("+") and not l.startswith("+++")]
        assert len(plus_lines) == 50, "Should NOT trim: 50 < 30*3=90"

    def test_regular_hunk_over_3x_threshold_is_trimmed(self):
        """A 100-line hunk with 10-line target (padded span ~20): 100 > 20*3=60, trim applies."""
        body = [f" context {n}" for n in range(1, 101)]  # 100 context lines starting at line 1
        hunk_header = "@@ -1,100 +1,100 @@"
        diff = _make_existing_file_diff(hunk_header, body)

        result = mod.filter_diff_hunks(diff, 50, 59)  # 10-line target, padded: 45-64 => span=20
        context_lines = [l for l in result.split("\n") if l.startswith(" context")]
        assert len(context_lines) < 100, "Should have trimmed: 100 > 20*3=60"
        assert len(context_lines) >= 15, "Should still contain the target range"

    def test_empty_diff_returns_unchanged(self):
        """Empty diff string should be returned as-is."""
        assert mod.filter_diff_hunks("", 10, 20) == ""

    def test_insertion_only_hunk_existing_file_keeps_3x_factor(self):
        """An insertion-only hunk in an existing file (--- a/file) uses 3x factor, not 1."""
        body = [f"+inserted {n}" for n in range(1, 51)]  # 50 insertion lines
        hunk_header = "@@ -10,0 +10,50 @@"
        diff = _make_existing_file_diff(hunk_header, body)

        # Target 20-30, padded: 15-35 => span=21. 50 < 21*3=63 => no trim.
        result = mod.filter_diff_hunks(diff, 20, 30)
        plus_lines = [l for l in result.split("\n") if l.startswith("+") and not l.startswith("+++")]
        assert len(plus_lines) == 50, "Existing-file insertion should use 3x factor, not trim"
