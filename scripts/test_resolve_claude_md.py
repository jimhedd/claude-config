"""Tests for resolve-claude-md.py."""

import importlib.util
import os
import subprocess
import tempfile

import pytest

# Import the script as a module
spec = importlib.util.spec_from_file_location(
    "resolve_claude_md",
    os.path.join(os.path.dirname(__file__), "resolve-claude-md.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


# --- compute_ancestor_dirs ---

class TestComputeAncestorDirs:
    def test_empty_input(self):
        result = mod.compute_ancestor_dirs([])
        assert "" in result  # root always included

    def test_single_file_at_root(self):
        result = mod.compute_ancestor_dirs(["README.md"])
        assert set(result) == {""}

    def test_single_nested_file(self):
        result = mod.compute_ancestor_dirs(["services/payments/handler.go"])
        assert set(result) == {"", "services", "services/payments"}

    def test_multiple_files_deduplication(self):
        result = mod.compute_ancestor_dirs([
            "services/payments/handler.go",
            "services/payments/util.go",
            "services/auth/login.go",
        ])
        expected = {"", "services", "services/payments", "services/auth"}
        assert set(result) == expected

    def test_deeply_nested(self):
        result = mod.compute_ancestor_dirs(["a/b/c/d/e.txt"])
        assert set(result) == {"", "a", "a/b", "a/b/c", "a/b/c/d"}


# --- sort_ancestor_dirs ---

class TestSortAncestorDirs:
    def test_deepest_first(self):
        dirs = ["", "services", "services/payments"]
        result = mod.sort_ancestor_dirs(dirs)
        assert result == ["services/payments", "services", ""]

    def test_lexicographic_within_depth(self):
        dirs = ["", "alpha", "beta", "alpha/deep", "beta/deep"]
        result = mod.sort_ancestor_dirs(dirs)
        assert result == ["alpha/deep", "beta/deep", "alpha", "beta", ""]

    def test_root_always_last(self):
        dirs = ["", "z"]
        result = mod.sort_ancestor_dirs(dirs)
        assert result[-1] == ""


# --- format_ancestor_dirs_list ---

class TestFormatAncestorDirsList:
    def test_root_only(self):
        assert mod.format_ancestor_dirs_list([""]) == "(root)"

    def test_mixed(self):
        result = mod.format_ancestor_dirs_list(["a/b", "a", ""])
        assert result == "a/b, a, (root)"


# --- is_inside_fenced_code_block ---

class TestIsInsideFencedCodeBlock:
    def test_not_in_fence(self):
        lines = ["hello", "world", "@foo.md"]
        assert mod.is_inside_fenced_code_block(lines, 2) is False

    def test_inside_backtick_fence(self):
        lines = ["```", "@foo.md", "```"]
        assert mod.is_inside_fenced_code_block(lines, 1) is True

    def test_after_closed_fence(self):
        lines = ["```", "code", "```", "@foo.md"]
        assert mod.is_inside_fenced_code_block(lines, 3) is False

    def test_inside_tilde_fence(self):
        lines = ["~~~", "@foo.md", "~~~"]
        assert mod.is_inside_fenced_code_block(lines, 1) is True

    def test_nested_fence_longer_inside(self):
        lines = ["```", "````", "code", "````", "@foo.md", "```"]
        # 4-backtick inside 3-backtick does not close outer
        assert mod.is_inside_fenced_code_block(lines, 4) is True

    def test_backtick_fence_with_backtick_in_info_string(self):
        # Per CommonMark: backtick fence info string cannot contain backticks
        lines = ["``` `foo`", "@foo.md", "```"]
        # This is NOT a valid fence opener, so line 1 is not in a fence
        assert mod.is_inside_fenced_code_block(lines, 1) is False

    def test_fence_with_info_string_no_backticks(self):
        lines = ["```python", "@foo.md", "```"]
        assert mod.is_inside_fenced_code_block(lines, 1) is True

    def test_indented_fence(self):
        lines = ["   ```", "@foo.md", "   ```"]
        assert mod.is_inside_fenced_code_block(lines, 1) is True


# --- is_inside_inline_code_span ---

class TestIsInsideInlineCodeSpan:
    def test_no_backticks(self):
        assert mod.is_inside_inline_code_span("@foo.md", 0) is False

    def test_inside_code_span(self):
        line = "see `@foo.md` for details"
        at_pos = line.index("@")
        assert mod.is_inside_inline_code_span(line, at_pos) is True

    def test_outside_code_span(self):
        line = "`code` @foo.md"
        at_pos = line.index("@")
        assert mod.is_inside_inline_code_span(line, at_pos) is False

    def test_double_backtick_span(self):
        line = "see ``@foo.md`` here"
        at_pos = line.index("@")
        assert mod.is_inside_inline_code_span(line, at_pos) is True

    def test_unmatched_single_then_double_backtick_span(self):
        # Per CommonMark: single backtick has no match, double-backtick pair
        # at positions 2-3 and 11-12 form a code span containing @foo.md
        line = "` ``@foo.md``"
        at_pos = line.index("@")
        assert mod.is_inside_inline_code_span(line, at_pos) is True


# --- is_directive_line ---

class TestIsDirectiveLine:
    def test_valid_directive(self):
        lines = ["@AGENTS.md"]
        assert mod.is_directive_line("@AGENTS.md", lines, 0) == "AGENTS.md"

    def test_with_leading_whitespace(self):
        lines = ["  @AGENTS.md"]
        assert mod.is_directive_line("  @AGENTS.md", lines, 0) == "AGENTS.md"

    def test_not_directive_mention(self):
        lines = ["@team please check"]
        assert mod.is_directive_line("@team please check", lines, 0) is None

    def test_not_directive_email(self):
        lines = ["email@example.com"]
        # @ is not first non-whitespace
        assert mod.is_directive_line("email@example.com", lines, 0) is None

    def test_dotdot_rejected(self):
        lines = ["@../secret.md"]
        assert mod.is_directive_line("@../secret.md", lines, 0) is None

    def test_absolute_path_rejected(self):
        lines = ["@/etc/passwd"]
        assert mod.is_directive_line("@/etc/passwd", lines, 0) is None

    def test_inside_code_block(self):
        lines = ["```", "@foo.md", "```"]
        assert mod.is_directive_line("@foo.md", lines, 1) is None

    def test_inside_inline_code(self):
        lines = ["`@foo.md`"]
        assert mod.is_directive_line("`@foo.md`", lines, 0) is None

    def test_shell_metacharacters(self):
        lines = ["@$(whoami).md"]
        assert mod.is_directive_line("@$(whoami).md", lines, 0) is None

    def test_tilde_in_path(self):
        lines = ["@~/config.md"]
        assert mod.is_directive_line("@~/config.md", lines, 0) == "~/config.md"


# --- path_escapes_root ---

class TestPathEscapesRoot:
    def test_safe_path(self):
        assert mod.path_escapes_root("foo/bar.md") is False

    def test_absolute_path(self):
        assert mod.path_escapes_root("/etc/passwd") is True

    def test_dotdot_escape(self):
        assert mod.path_escapes_root("../secret.md") is True

    def test_normalized_escape(self):
        assert mod.path_escapes_root("foo/../../secret") is True

    def test_deep_safe(self):
        assert mod.path_escapes_root("a/b/c/d.md") is False


# --- get_parent_dir ---

class TestGetParentDir:
    def test_root_claude_md(self):
        assert mod.get_parent_dir("CLAUDE.md") == ""

    def test_root_dotclaude(self):
        assert mod.get_parent_dir(".claude/CLAUDE.md") == ""

    def test_nested_claude_md(self):
        assert mod.get_parent_dir("services/CLAUDE.md") == "services"

    def test_nested_dotclaude(self):
        assert mod.get_parent_dir("services/.claude/CLAUDE.md") == "services"

    def test_deeply_nested(self):
        assert mod.get_parent_dir("a/b/c/CLAUDE.md") == "a/b/c"


# --- resolve_path ---

class TestResolvePath:
    def test_root_directive(self):
        assert mod.resolve_path("AGENTS.md", "") == "AGENTS.md"

    def test_nested_directive(self):
        assert mod.resolve_path("AGENTS.md", "services") == "services/AGENTS.md"

    def test_relative_path(self):
        assert mod.resolve_path("../shared.md", "a/b") == "a/shared.md"


# --- resolve_directives_in_content ---

class TestResolveDirectivesInContent:
    def test_no_directives(self):
        ctx = mod.ResolveContext(
            git_dir="/tmp", merge_base=None, working_tree=True,
            depth_limit=5, budget=8000, budget_remaining=8000,
        )
        content = "# Title\nSome text\n"
        result, directives = mod.resolve_directives_in_content(
            content, "", "CLAUDE.md", ctx, 1, set(),
        )
        assert result == content
        assert directives == []

    def test_depth_limit_exceeded(self):
        ctx = mod.ResolveContext(
            git_dir="/tmp", merge_base=None, working_tree=True,
            depth_limit=2, budget=8000, budget_remaining=8000,
        )
        content = "@foo.md"
        result, directives = mod.resolve_directives_in_content(
            content, "", "CLAUDE.md", ctx, 3, set(),
        )
        # Should return content unchanged when depth > limit
        assert result == content
        assert directives == []

    def test_cycle_detection(self):
        ctx = mod.ResolveContext(
            git_dir="/tmp", merge_base=None, working_tree=True,
            depth_limit=5, budget=8000, budget_remaining=8000,
        )
        content = "@CLAUDE.md"
        result, directives = mod.resolve_directives_in_content(
            content, "", "CLAUDE.md", ctx, 1, {"CLAUDE.md"},
        )
        assert len(directives) == 1
        assert directives[0].status == "cycle-skipped"

    def test_not_found_file(self):
        ctx = mod.ResolveContext(
            git_dir="/tmp", merge_base=None, working_tree=True,
            depth_limit=5, budget=8000, budget_remaining=8000,
        )
        content = "@nonexistent-file.md"
        result, directives = mod.resolve_directives_in_content(
            content, "", "CLAUDE.md", ctx, 1, set(),
        )
        assert len(directives) == 1
        assert directives[0].status == "not-found"
        assert directives[0].exists_at_merge_base is False

    def test_budget_dropped_before_existence_check(self):
        # Budget < 12 triggers budget-dropped before file read
        # but only after existence check passes. With non-existent file,
        # not-found takes priority per status priority rules.
        ctx = mod.ResolveContext(
            git_dir="/tmp", merge_base=None, working_tree=True,
            depth_limit=5, budget=8000, budget_remaining=5,
        )
        content = "@nonexistent.md"
        result, directives = mod.resolve_directives_in_content(
            content, "", "CLAUDE.md", ctx, 1, set(),
        )
        assert len(directives) == 1
        # not-found takes priority over budget-dropped per status priority
        assert directives[0].status == "not-found"

    def test_resolved_happy_path(self, tmp_path):
        """Test successful directive resolution with an existing file."""
        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text("Agent instructions here")
        ctx = mod.ResolveContext(
            git_dir=str(tmp_path), merge_base=None, working_tree=True,
            depth_limit=5, budget=8000, budget_remaining=8000,
        )
        content = "# Title\n@AGENTS.md\nMore text"
        result, directives = mod.resolve_directives_in_content(
            content, "", "CLAUDE.md", ctx, 1, set(),
        )
        assert "Agent instructions here" in result
        assert "@AGENTS.md" not in result
        assert "# Title" in result
        assert "More text" in result
        assert len(directives) == 1
        assert directives[0].status == "resolved"
        assert directives[0].resolved_path == "AGENTS.md"
        assert ctx.budget_remaining == 8000 - len("Agent instructions here")

    def test_budget_truncation(self, tmp_path):
        """Test that large files are truncated when exceeding budget."""
        big_file = tmp_path / "big.md"
        big_file.write_text("x" * 100)
        ctx = mod.ResolveContext(
            git_dir=str(tmp_path), merge_base=None, working_tree=True,
            depth_limit=5, budget=8000, budget_remaining=50,
        )
        content = "@big.md"
        result, directives = mod.resolve_directives_in_content(
            content, "", "CLAUDE.md", ctx, 1, set(),
        )
        assert len(directives) == 1
        assert directives[0].status == "truncated"
        assert result.endswith("[truncated]")
        # available = 50 - 11 = 39 chars of content + 11 chars "[truncated]"
        assert len(result) == 50
        assert ctx.budget_remaining == 0

    def test_budget_dropped_existing_file(self, tmp_path):
        """Test budget-dropped when file exists but budget < 12."""
        existing = tmp_path / "small.md"
        existing.write_text("small content")
        ctx = mod.ResolveContext(
            git_dir=str(tmp_path), merge_base=None, working_tree=True,
            depth_limit=5, budget=8000, budget_remaining=10,
        )
        content = "@small.md"
        result, directives = mod.resolve_directives_in_content(
            content, "", "CLAUDE.md", ctx, 1, set(),
        )
        assert len(directives) == 1
        assert directives[0].status == "budget-dropped"
        # Directive line should be dropped from output
        assert "@small.md" not in result

    def test_recursive_resolution(self, tmp_path):
        """Test that directives in included files are resolved recursively."""
        inner = tmp_path / "inner.md"
        inner.write_text("inner content")
        outer = tmp_path / "outer.md"
        outer.write_text("outer start\n@inner.md\nouter end")
        ctx = mod.ResolveContext(
            git_dir=str(tmp_path), merge_base=None, working_tree=True,
            depth_limit=5, budget=8000, budget_remaining=8000,
        )
        content = "@outer.md"
        result, directives = mod.resolve_directives_in_content(
            content, "", "CLAUDE.md", ctx, 1, set(),
        )
        assert "inner content" in result
        assert "outer start" in result
        assert "outer end" in result
        assert len(directives) == 2
        assert directives[0].status == "resolved"
        assert directives[0].resolved_path == "outer.md"
        assert directives[1].status == "resolved"
        assert directives[1].resolved_path == "inner.md"


# --- Integration test for main() ---

class TestMainIntegration:
    def _init_git_repo(self, repo_dir):
        """Initialize a git repo with a CLAUDE.md and target file."""
        subprocess.run(
            ["git", "init"], cwd=str(repo_dir),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=str(repo_dir), capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(repo_dir), capture_output=True, check=True,
        )

    def test_json_output_structure(self, tmp_path):
        """Test that main() produces valid JSON with expected keys."""
        self._init_git_repo(tmp_path)
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Agent rules")
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n@AGENTS.md\n")
        # Also create a changed file for ref-range
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("print('hello')")
        subprocess.run(
            ["git", "add", "."], cwd=str(tmp_path),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path), capture_output=True, check=True,
        )
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        # Make a change
        (src / "main.py").write_text("print('updated')")
        subprocess.run(
            ["git", "add", "."], cwd=str(tmp_path),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "update"],
            cwd=str(tmp_path), capture_output=True, check=True,
        )
        script = os.path.join(os.path.dirname(__file__), "resolve-claude-md.py")
        result = subprocess.run(
            [
                "python3", script,
                "--git-dir", str(tmp_path),
                "--merge-base", base,
                "--ref-range", f"{base}..HEAD",
                "--depth", "5",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        import json
        output = json.loads(result.stdout)
        # Verify all expected keys
        for key in [
            "ancestor_dirs_list", "expected_guidelines", "expected_directives",
            "pr_added_guidelines", "warnings", "guidelines_loaded_section",
            "resolved_content",
        ]:
            assert key in output, f"Missing key: {key}"
        # Verify guideline was found
        guideline_paths = [g["path"] for g in output["expected_guidelines"]]
        assert "CLAUDE.md" in guideline_paths
        # Verify directive was resolved
        assert any(
            d["directive"] == "AGENTS.md" for d in output["expected_directives"]
        )
        # Verify resolved_content contains the agent rules
        assert "Agent rules" in output["resolved_content"]
        # Verify guidelines_loaded_section format
        gl = output["guidelines_loaded_section"]
        assert "CLAUDE.md" in gl
        assert "@AGENTS.md" in gl

    def test_files_flag_discovers_ancestor_dirs(self, tmp_path):
        """Test --files discovers correct ancestor dirs and resolves CLAUDE.md."""
        self._init_git_repo(tmp_path)
        # Create root CLAUDE.md with @AGENTS.md directive
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n@AGENTS.md\n")
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Agent rules from AGENTS.md")
        # Create a file at src/services/handler.py
        src_services = tmp_path / "src" / "services"
        src_services.mkdir(parents=True)
        (src_services / "handler.py").write_text("print('handler')")
        subprocess.run(
            ["git", "add", "."], cwd=str(tmp_path),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path), capture_output=True, check=True,
        )
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        script = os.path.join(os.path.dirname(__file__), "resolve-claude-md.py")
        result = subprocess.run(
            [
                "python3", script,
                "--git-dir", str(tmp_path),
                "--merge-base", base,
                "--files", "src/services/handler.py",
                "--depth", "5",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        import json
        output = json.loads(result.stdout)
        # Verify ancestor dirs include src/services, src, (root)
        ancestor_dirs = output["ancestor_dirs_list"]
        assert "src/services" in ancestor_dirs
        assert "src" in ancestor_dirs
        assert "(root)" in ancestor_dirs
        # Verify AGENTS.md directive was expanded
        assert "Agent rules from AGENTS.md" in output["resolved_content"]

    def test_files_and_ref_range_mutually_exclusive(self, tmp_path):
        """Test --files and --ref-range cannot be used together."""
        self._init_git_repo(tmp_path)
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(tmp_path),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path), capture_output=True, check=True,
        )
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        script = os.path.join(os.path.dirname(__file__), "resolve-claude-md.py")
        result = subprocess.run(
            [
                "python3", script,
                "--git-dir", str(tmp_path),
                "--merge-base", base,
                "--ref-range", f"{base}..HEAD",
                "--files", "src/main.py",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "mutually exclusive" in result.stderr

    def test_files_flag_handles_paths_with_spaces(self, tmp_path):
        """Test --files handles paths with spaces correctly."""
        self._init_git_repo(tmp_path)
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n")
        # Create a file at "src/my module/handler.py"
        my_module = tmp_path / "src" / "my module"
        my_module.mkdir(parents=True)
        (my_module / "handler.py").write_text("print('handler')")
        subprocess.run(
            ["git", "add", "."], cwd=str(tmp_path),
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=str(tmp_path), capture_output=True, check=True,
        )
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(tmp_path),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        script = os.path.join(os.path.dirname(__file__), "resolve-claude-md.py")
        result = subprocess.run(
            [
                "python3", script,
                "--git-dir", str(tmp_path),
                "--merge-base", base,
                "--files", "src/my module/handler.py",
                "--depth", "5",
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Script failed: {result.stderr}"
        import json
        output = json.loads(result.stdout)
        # Verify ancestor dirs include "src/my module", "src", "(root)"
        ancestor_dirs = output["ancestor_dirs_list"]
        assert "src/my module" in ancestor_dirs
        assert "src" in ancestor_dirs
        assert "(root)" in ancestor_dirs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
