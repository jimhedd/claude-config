"""Tests for render-report.py — HTML body fragment and pairs TSV generation."""

import importlib.util
import json
import os
import subprocess
import tempfile

import pytest

# Import the script as a module (same pattern as test_inject_diff.py)
spec = importlib.util.spec_from_file_location(
    "render_report",
    os.path.join(os.path.dirname(__file__), "render-report.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


# --- Fixtures ---


def _minimal_pr():
    return {
        "number": 42,
        "title": "Fix auth bug",
        "base_ref": "main",
        "head_ref": "feature/auth-fix",
        "base_sha": "abc12345",
        "head_sha": "def67890",
        "additions": 150,
        "deletions": 30,
        "changed_files": 5,
    }


def _minimal_verdicts(overall="REQUEST_CHANGES"):
    return {
        "bug": "APPROVE",
        "arch": "APPROVE",
        "quality": "REQUEST_CHANGES",
        "tests": "REQUEST_CHANGES",
        "overall": overall,
    }


def _sample_issue(issue_id="P0-1", tier="p0", **overrides):
    base = {
        "id": issue_id,
        "tier": tier,
        "title": "Null pointer in handler",
        "reviewer": "bug",
        "severity": "high",
        "category": "null-safety",
        "file": "src/auth/handler.kt",
        "line_range": "42-48",
        "problem": "The `authToken` can be null when session expires.",
        "suggestion": "Add a null check before accessing `authToken.value`.",
    }
    base.update(overrides)
    return base


def _full_data(**overrides):
    data = {
        "pr": _minimal_pr(),
        "verdicts": _minimal_verdicts(),
        "issues": [
            _sample_issue("P0-1", "p0"),
            _sample_issue("P1-1", "p1", title="Missing validation", severity="medium",
                          category="input-validation", file="src/api/endpoint.kt",
                          line_range="10-15", reviewer="arch"),
            _sample_issue("P2-1", "p2", title="Unused import", severity="low",
                          category="style", file="src/util.kt", line_range=None,
                          reviewer="quality"),
            _sample_issue("N-1", "nitpick", title="Rename variable", severity="nitpick",
                          category="naming", file="src/config.kt", line_range="5-5",
                          reviewer="quality"),
        ],
        "guidelines": {
            "expected_files": ["CLAUDE.md"],
            "expected_directives": [],
            "pr_added_files": [],
            "reviewers": {
                "bug": {"files_count": 1, "directives_count": 0, "matched": True},
                "arch": {"files_count": 1, "directives_count": 0, "matched": True},
                "quality": {"files_count": 1, "directives_count": 0, "matched": True},
                "tests": {"files_count": 1, "directives_count": 0, "matched": True},
            },
            "warnings": [],
        },
    }
    data.update(overrides)
    return data


# --- Unit tests for utility functions ---


class TestEsc:
    def test_escapes_html_entities(self):
        assert mod.esc('<script>"hello"&') == '&lt;script&gt;&quot;hello&quot;&amp;'

    def test_empty_string(self):
        assert mod.esc("") == ""


class TestProseToHtml:
    def test_backtick_conversion(self):
        result = mod.prose_to_html("Use `foo.bar()` for access")
        assert "<code>foo.bar()</code>" in result

    def test_fenced_code_block(self):
        text = "Example:\n```\nval x = 1\nval y = 2\n```\nDone."
        result = mod.prose_to_html(text)
        assert "<pre><code>" in result
        assert "val x = 1" in result
        assert "</code></pre>" in result

    def test_html_escaping(self):
        result = mod.prose_to_html("Check if x < 10 && y > 5")
        assert "&lt;" in result
        assert "&amp;&amp;" in result

    def test_mixed_content(self):
        text = "Call `getData()` with:\n```\ngetData<T>()\n```\nThat's it."
        result = mod.prose_to_html(text)
        assert "<code>getData()</code>" in result
        assert "<pre><code>" in result
        assert "getData&lt;T&gt;()" in result

    def test_empty_string(self):
        assert mod.prose_to_html("") == ""

    def test_multiple_backticks_in_line(self):
        result = mod.prose_to_html("Use `foo` and `bar` together")
        assert result.count("<code>") == 2
        assert "<code>foo</code>" in result
        assert "<code>bar</code>" in result


class TestFileDisplay:
    def test_with_line_range(self):
        short, full = mod.file_display("src/auth/handler.kt", "42-48")
        assert short == "handler.kt:42-48"
        assert full == "src/auth/handler.kt:42-48"

    def test_without_line_range(self):
        short, full = mod.file_display("src/util.kt", None)
        assert short == "util.kt"
        assert full == "src/util.kt"


# --- Validation tests ---


class TestValidateInput:
    def test_valid_data(self):
        errors = mod.validate_input(_full_data())
        assert errors == []

    def test_duplicate_ids_rejected(self):
        data = _full_data(issues=[
            _sample_issue("P0-1", "p0"),
            _sample_issue("P0-1", "p1", title="Duplicate"),
        ])
        errors = mod.validate_input(data)
        assert any("duplicate id" in e for e in errors)

    def test_malformed_line_range_rejected(self):
        data = _full_data(issues=[
            _sample_issue("P0-1", "p0", line_range="abc"),
        ])
        errors = mod.validate_input(data)
        assert any("invalid line_range" in e for e in errors)

    def test_line_range_start_gt_end_rejected(self):
        data = _full_data(issues=[
            _sample_issue("P0-1", "p0", line_range="50-10"),
        ])
        errors = mod.validate_input(data)
        assert any("start" in e and "end" in e for e in errors)

    def test_single_line_normalized(self):
        issue = _sample_issue("P0-1", "p0", line_range="12")
        data = _full_data(issues=[issue])
        errors = mod.validate_input(data)
        assert errors == []
        assert issue["line_range"] == "12-12"

    def test_missing_required_field_rejected(self):
        issue = _sample_issue("P0-1", "p0")
        del issue["title"]
        data = _full_data(issues=[issue])
        errors = mod.validate_input(data)
        assert any("missing required field 'title'" in e for e in errors)

    def test_invalid_tier_rejected(self):
        data = _full_data(issues=[
            _sample_issue("P0-1", "invalid_tier"),
        ])
        errors = mod.validate_input(data)
        assert any("tier must be one of" in e for e in errors)

    def test_invalid_verdict_rejected(self):
        data = _full_data()
        data["verdicts"]["overall"] = "MAYBE"
        errors = mod.validate_input(data)
        assert any("verdicts.overall" in e for e in errors)

    def test_missing_top_level_keys(self):
        errors = mod.validate_input({})
        assert len(errors) == 3  # pr, verdicts, issues

    def test_null_line_range_accepted(self):
        data = _full_data(issues=[
            _sample_issue("P0-1", "p0", line_range=None),
        ])
        errors = mod.validate_input(data)
        assert errors == []

    def test_missing_pr_fields_rejected(self):
        data = _full_data()
        data["pr"] = {}
        errors = mod.validate_input(data)
        assert any("pr: missing required field" in e for e in errors)
        assert len(errors) >= 9  # all 9 pr fields missing


# --- Render function tests ---


class TestRenderHeader:
    def test_contains_pr_number_and_title(self):
        html = mod.render_header(_minimal_pr())
        assert "PR #42" in html
        assert "Fix auth bug" in html

    def test_contains_refs_and_shas(self):
        html = mod.render_header(_minimal_pr())
        assert "main" in html
        assert "feature/auth-fix" in html
        assert "abc12345" in html
        assert "def67890" in html

    def test_contains_stats(self):
        html = mod.render_header(_minimal_pr())
        assert "+150" in html
        assert "-30" in html
        assert "5 files" in html

    def test_contains_timestamp_placeholders(self):
        html = mod.render_header(_minimal_pr())
        assert "{{GENERATED_UTC}}" in html
        assert "{{GENERATED_ISO}}" in html

    def test_singular_file_count(self):
        pr = _minimal_pr()
        pr["changed_files"] = 1
        html = mod.render_header(pr)
        assert "1 file" in html
        assert "1 files" not in html

    def test_html_escapes_title(self):
        pr = _minimal_pr()
        pr["title"] = 'Fix <script> "injection"'
        html = mod.render_header(pr)
        assert "&lt;script&gt;" in html
        assert "&quot;injection&quot;" in html


class TestRenderSummaryBar:
    def test_approve_badges(self):
        html = mod.render_summary_bar(
            _minimal_verdicts(overall="APPROVE"),
            {"p0": 0, "p1": 0, "p2": 0, "nitpick": 0},
        )
        assert "badge-approve" in html
        assert "Overall: APPROVE" in html

    def test_request_changes_badges(self):
        html = mod.render_summary_bar(
            _minimal_verdicts(),
            {"p0": 1, "p1": 2, "p2": 3, "nitpick": 1},
        )
        assert "badge-request-changes" in html
        assert "1 P0" in html
        assert "2 P1" in html

    def test_zero_chips_have_chip_zero_class(self):
        html = mod.render_summary_bar(
            _minimal_verdicts(),
            {"p0": 0, "p1": 1, "p2": 0, "nitpick": 0},
        )
        assert "chip-zero" in html


class TestRenderGuidelines:
    def test_no_guidelines(self):
        html = mod.render_guidelines({"expected_files": [], "expected_directives": [],
                                       "pr_added_files": [], "reviewers": {}, "warnings": []})
        assert "No CLAUDE.md files found" in html

    def test_with_expected_files(self):
        guidelines = {
            "expected_files": ["CLAUDE.md"],
            "expected_directives": [],
            "pr_added_files": [],
            "reviewers": {
                "bug": {"files_count": 1, "directives_count": 0, "matched": True},
            },
            "warnings": [],
        }
        html = mod.render_guidelines(guidelines)
        assert "CLAUDE.md" in html
        assert "guidelines-table" in html

    def test_reviewer_mismatch(self):
        guidelines = {
            "expected_files": ["CLAUDE.md"],
            "expected_directives": [],
            "pr_added_files": [],
            "reviewers": {
                "bug": {"files_count": 1, "directives_count": 0, "matched": True},
                "arch": {"files_count": 0, "directives_count": 0, "matched": False},
            },
            "warnings": [],
        }
        html = mod.render_guidelines(guidelines)
        assert "guidelines-ok" in html  # bug matched
        assert "guidelines-warn" in html  # arch mismatched
        assert "mismatch" in html

    def test_with_directives(self):
        guidelines = {
            "expected_files": ["CLAUDE.md"],
            "expected_directives": [
                {"parent_path": "CLAUDE.md", "directive_text": "@AGENTS.md",
                 "resolved_path": "AGENTS.md", "exists_at_merge_base": True},
            ],
            "pr_added_files": [],
            "reviewers": {},
            "warnings": [],
        }
        html = mod.render_guidelines(guidelines)
        assert "@AGENTS.md" in html
        assert "&rarr;" in html

    def test_with_warnings(self):
        guidelines = {
            "expected_files": ["CLAUDE.md"],
            "expected_directives": [],
            "pr_added_files": [],
            "reviewers": {},
            "warnings": ["arch-reviewer loaded CLAUDE.md from working tree"],
        }
        html = mod.render_guidelines(guidelines)
        assert "guidelines-warnings" in html
        assert "guidelines-warn" in html

    def test_with_pr_added_files(self):
        guidelines = {
            "expected_files": ["CLAUDE.md"],
            "expected_directives": [],
            "pr_added_files": ["services/new-service/CLAUDE.md"],
            "reviewers": {},
            "warnings": [],
        }
        html = mod.render_guidelines(guidelines)
        assert "guidelines-notice" in html
        assert "services/new-service/CLAUDE.md" in html

    def test_none_guidelines(self):
        html = mod.render_guidelines(None)
        assert "No CLAUDE.md files found" in html


class TestRenderToc:
    def test_contains_all_tiers(self):
        issues_by_tier = {
            "p0": [_sample_issue("P0-1", "p0")],
            "p1": [_sample_issue("P1-1", "p1")],
        }
        html = mod.render_toc(issues_by_tier)
        assert "toc-p0" in html
        assert "toc-p1" in html
        assert 'href="#P0-1"' in html
        assert 'href="#P1-1"' in html

    def test_omits_empty_tiers(self):
        issues_by_tier = {"p0": [_sample_issue("P0-1", "p0")]}
        html = mod.render_toc(issues_by_tier)
        assert "toc-p0" in html
        assert "toc-p1" not in html

    def test_tier_labels_include_counts(self):
        issues_by_tier = {
            "p0": [_sample_issue("P0-1", "p0"), _sample_issue("P0-2", "p0")],
        }
        html = mod.render_toc(issues_by_tier)
        assert "P0 — Must Fix (2)" in html  # raw HTML, the em dash is literal

    def test_copy_all_button(self):
        issues_by_tier = {"p0": [_sample_issue("P0-1", "p0")]}
        html = mod.render_toc(issues_by_tier)
        assert 'class="copy-all-md"' in html
        assert 'type="button"' in html


class TestRenderToggleBar:
    def test_has_four_buttons(self):
        html = mod.render_toggle_bar()
        assert html.count("toggle-btn") == 4
        assert 'data-action="expand"' in html
        assert 'data-action="collapse"' in html


class TestRenderIssueCard:
    def test_card_structure(self):
        html = mod.render_issue_card(_sample_issue())
        assert 'id="P0-1"' in html
        assert 'class="card"' in html
        assert "[P0-1]" in html
        assert "Null pointer in handler" in html

    def test_file_display_basename(self):
        html = mod.render_issue_card(_sample_issue())
        # Short display should be basename
        assert "handler.kt:42-48" in html
        # Full path in title
        assert 'title="src/auth/handler.kt:42-48"' in html

    def test_diff_open_for_p0(self):
        html = mod.render_issue_card(_sample_issue(tier="p0"))
        assert 'class="diff-container" open' in html

    def test_diff_open_for_p1(self):
        html = mod.render_issue_card(_sample_issue(tier="p1"))
        assert 'class="diff-container" open' in html

    def test_diff_closed_for_p2(self):
        html = mod.render_issue_card(_sample_issue(tier="p2"))
        assert 'class="diff-container">' in html  # no open attribute

    def test_diff_closed_for_nitpick(self):
        html = mod.render_issue_card(_sample_issue(tier="nitpick"))
        assert 'class="diff-container">' in html

    def test_diff_placeholder(self):
        html = mod.render_issue_card(_sample_issue())
        assert '<script type="application/diff" data-for="P0-1"></script>' in html

    def test_diff_viewer(self):
        html = mod.render_issue_card(_sample_issue())
        assert 'data-diff-id="P0-1"' in html

    def test_copy_button(self):
        html = mod.render_issue_card(_sample_issue())
        assert 'class="copy-md"' in html
        assert 'data-issue="P0-1"' in html

    def test_markdown_block(self):
        html = mod.render_issue_card(_sample_issue())
        assert 'type="text/markdown"' in html
        assert "## [P0-1]" in html
        assert "src/auth/handler.kt:42-48" in html

    def test_markdown_uses_full_path(self):
        html = mod.render_issue_card(_sample_issue())
        # Markdown block should use full path
        assert "`src/auth/handler.kt:42-48`" in html

    def test_prose_converted_in_problem(self):
        issue = _sample_issue(problem="Check `authToken` value")
        html = mod.render_issue_card(issue)
        assert "<code>authToken</code>" in html

    def test_script_tag_escaping_in_markdown(self):
        issue = _sample_issue(suggestion="Don't use </script> tag")
        html = mod.render_issue_card(issue)
        assert "<\\/script" in html


class TestRenderTierSection:
    def test_tier_section(self):
        issues = [_sample_issue("P0-1", "p0"), _sample_issue("P0-2", "p0")]
        html = mod.render_tier_section("p0", issues)
        assert 'class="tier-p0"' in html
        assert "P0 — Must Fix (2)" in html  # raw HTML
        assert 'id="P0-1"' in html
        assert 'id="P0-2"' in html
        assert "back-to-top" in html


# --- Top-level generator tests ---


class TestGenerateBody:
    def test_full_data(self):
        html = mod.generate_body(_full_data())
        assert "<header>" in html
        assert "summary" in html
        assert "guidelines" in html
        assert "<nav" in html
        assert "toggle-bar" in html
        assert "<main>" in html

    def test_zero_issues(self):
        data = _full_data(issues=[])
        html = mod.generate_body(data)
        assert "<header>" in html
        assert "guidelines" in html
        # No TOC, toggle bar, or main
        assert "<nav" not in html
        assert "toggle-bar" not in html
        assert "<main>" not in html

    def test_only_some_tiers(self):
        data = _full_data(issues=[_sample_issue("P0-1", "p0")])
        html = mod.generate_body(data)
        assert "tier-p0" in html
        assert "tier-p1" not in html


class TestGeneratePairsTsv:
    def test_basic_output(self):
        issues = [
            {"id": "P0-1", "file": "src/main.kt", "line_range": "42-48"},
            {"id": "P1-2", "file": "src/util.kt", "line_range": "12-12"},
            {"id": "P2-3", "file": "src/config.kt", "line_range": None},
        ]
        tsv = mod.generate_pairs_tsv(issues)
        lines = tsv.strip().split("\n")
        assert len(lines) == 3
        assert lines[0] == "P0-1\tsrc/main.kt\t42-48"
        assert lines[1] == "P1-2\tsrc/util.kt\t12-12"
        assert lines[2] == "P2-3\tsrc/config.kt"

    def test_empty_issues(self):
        assert mod.generate_pairs_tsv([]) == ""

    def test_matches_inject_diff_format(self):
        """Verify TSV output is parseable by inject-diff.py's parse_pairs_file()."""
        issues = [
            {"id": "P0-1", "file": "src/main.kt", "line_range": "42-48"},
            {"id": "N-1", "file": "src/config.kt", "line_range": None},
        ]
        tsv = mod.generate_pairs_tsv(issues)

        # Write to temp file and parse with inject-diff.py's parser
        inject_spec = importlib.util.spec_from_file_location(
            "inject_diff",
            os.path.join(os.path.dirname(__file__), "inject-diff.py"),
        )
        inject_mod = importlib.util.module_from_spec(inject_spec)
        inject_spec.loader.exec_module(inject_mod)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".tsv", delete=False) as f:
            f.write(tsv)
            tmp_path = f.name

        try:
            pairs = inject_mod.parse_pairs_file(tmp_path)
            assert len(pairs) == 2
            assert pairs[0] == ("P0-1", "src/main.kt", 42, 48)
            assert pairs[1] == ("N-1", "src/config.kt", None, None)
        finally:
            os.unlink(tmp_path)


# --- DOM-contract integration test ---


class TestDomContract:
    """Verify the HTML output contains the specific classes and data attributes
    that the template JS depends on."""

    @pytest.fixture
    def full_html(self):
        return mod.generate_body(_full_data())

    def test_copy_md_buttons_per_card(self, full_html):
        # Each card should have a .copy-md[data-issue] button
        for iid in ("P0-1", "P1-1", "P2-1", "N-1"):
            assert f'data-issue="{iid}"' in full_html

    def test_diff_viewer_per_card(self, full_html):
        for iid in ("P0-1", "P1-1", "P2-1", "N-1"):
            assert f'data-diff-id="{iid}"' in full_html

    def test_diff_placeholders_per_card(self, full_html):
        for iid in ("P0-1", "P1-1", "P2-1", "N-1"):
            assert f'<script type="application/diff" data-for="{iid}"></script>' in full_html

    def test_markdown_blocks_per_card(self, full_html):
        for iid in ("P0-1", "P1-1", "P2-1", "N-1"):
            assert f'<script type="text/markdown" data-for="{iid}">' in full_html

    def test_card_ids(self, full_html):
        for iid in ("P0-1", "P1-1", "P2-1", "N-1"):
            assert f'id="{iid}"' in full_html

    def test_toc_anchors_match_card_ids(self, full_html):
        for iid in ("P0-1", "P1-1", "P2-1", "N-1"):
            assert f'href="#{iid}"' in full_html


# --- Assemble pipeline smoke test ---


class TestAssemblePipelineSmoke:
    """Render a fixture JSON, call assemble-report.py on the output,
    assert the final HTML is valid."""

    def test_render_then_assemble(self):
        data = _full_data()
        body_html = mod.generate_body(data)

        script_dir = os.path.dirname(__file__)
        assemble_script = os.path.join(script_dir, "assemble-report.py")

        with tempfile.TemporaryDirectory() as tmpdir:
            body_path = os.path.join(tmpdir, "body.html")
            output_path = os.path.join(tmpdir, "report.html")

            with open(body_path, "w", encoding="utf-8") as f:
                f.write(body_html)

            result = subprocess.run(
                [
                    "python3", assemble_script,
                    body_path, output_path,
                    "--title", "PR Review: #42 - Fix auth bug",
                ],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, f"assemble-report.py failed: {result.stderr}"

            with open(output_path, "r", encoding="utf-8") as f:
                final_html = f.read()

            # Basic validity checks
            assert "<!DOCTYPE html>" in final_html
            assert "<body>" in final_html
            assert "</body>" in final_html

            # All card IDs present
            for iid in ("P0-1", "P1-1", "P2-1", "N-1"):
                assert f'id="{iid}"' in final_html

            # No unresolved {{BODY}} placeholder
            assert "{{BODY}}" not in final_html

            # Timestamp placeholders replaced
            assert "{{GENERATED_UTC}}" not in final_html
            assert "{{GENERATED_ISO}}" not in final_html

    def test_zero_issues_assembly(self):
        data = _full_data(issues=[])
        data["verdicts"] = {
            "bug": "APPROVE", "arch": "APPROVE",
            "quality": "APPROVE", "tests": "APPROVE",
            "overall": "APPROVE",
        }
        body_html = mod.generate_body(data)

        script_dir = os.path.dirname(__file__)
        assemble_script = os.path.join(script_dir, "assemble-report.py")

        with tempfile.TemporaryDirectory() as tmpdir:
            body_path = os.path.join(tmpdir, "body.html")
            output_path = os.path.join(tmpdir, "report.html")

            with open(body_path, "w", encoding="utf-8") as f:
                f.write(body_html)

            result = subprocess.run(
                [
                    "python3", assemble_script,
                    body_path, output_path,
                    "--title", "PR Review: #42 - All clean",
                ],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, f"assemble-report.py failed: {result.stderr}"

            with open(output_path, "r", encoding="utf-8") as f:
                final_html = f.read()

            assert "<!DOCTYPE html>" in final_html
            assert "{{BODY}}" not in final_html
            assert "Overall: APPROVE" in final_html


# --- CLI integration test ---


class TestCLI:
    def test_end_to_end(self):
        data = _full_data()

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "input.json")
            body_path = os.path.join(tmpdir, "body.html")
            pairs_path = os.path.join(tmpdir, "pairs.tsv")

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f)

            script = os.path.join(os.path.dirname(__file__), "render-report.py")
            result = subprocess.run(
                ["python3", script, json_path, body_path, pairs_path],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, f"render-report.py failed: {result.stderr}"

            with open(body_path, "r", encoding="utf-8") as f:
                body_html = f.read()
            with open(pairs_path, "r", encoding="utf-8") as f:
                pairs_tsv = f.read()

            assert "<header>" in body_html
            assert "P0-1" in body_html
            assert "P0-1\tsrc/auth/handler.kt\t42-48" in pairs_tsv

    def test_invalid_json_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "bad.json")
            body_path = os.path.join(tmpdir, "body.html")
            pairs_path = os.path.join(tmpdir, "pairs.tsv")

            with open(json_path, "w") as f:
                f.write("{invalid json")

            script = os.path.join(os.path.dirname(__file__), "render-report.py")
            result = subprocess.run(
                ["python3", script, json_path, body_path, pairs_path],
                capture_output=True, text=True,
            )
            assert result.returncode != 0

    def test_validation_error_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, "bad.json")
            body_path = os.path.join(tmpdir, "body.html")
            pairs_path = os.path.join(tmpdir, "pairs.tsv")

            with open(json_path, "w") as f:
                json.dump({"pr": {}, "verdicts": {}, "issues": []}, f)

            script = os.path.join(os.path.dirname(__file__), "render-report.py")
            result = subprocess.run(
                ["python3", script, json_path, body_path, pairs_path],
                capture_output=True, text=True,
            )
            assert result.returncode != 0
            assert "Validation errors" in result.stderr
