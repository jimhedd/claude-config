---
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Task, AskUserQuestion
description: "Implement a plan file with a simple 4-agent review loop"
requires-argument: false
argument-hint: "plan-name.md [--squash|--no-squash]"
argument-description: "Optional plan file. If omitted, choose from recent files in ~/.claude/plans"
---

# Implement with Review Loop

Implement a plan, code normally, and use a tight 4-agent review loop until all reviewers approve.

## Phase 0: Preflight

1. Confirm git repo: `git rev-parse --is-inside-work-tree`.
2. Capture `repo_root`: `git rev-parse --show-toplevel`.
3. Require clean tree: `git -C {repo_root} status --porcelain` must be empty.
4. Capture `branch`: `git -C {repo_root} rev-parse --abbrev-ref HEAD`.
5. Extract optional `jira_ticket` from branch via `[A-Z][A-Z0-9]+-[0-9]+`.

If any step fails, stop and report the error.

## Phase 1: Resolve Plan and Squash Mode

1. Parse optional flags from `$ARGUMENTS`:
   - `--squash` => `squash_mode=on`
   - `--no-squash` => `squash_mode=off`
   - if both are present, stop with error
2. Resolve plan path from remaining argument:
   - absolute path => use as-is
   - relative path => resolve from current directory
   - bare `*.md` => also try `~/.claude/plans/<filename>`
3. If no plan argument:
   - list 5 most recent `~/.claude/plans/*.md`
   - ask user to choose via `AskUserQuestion`
   - if none selected, stop
4. Read selected plan into `{plan_contents}`.
5. If CLI did not set squash mode, read optional plan-level override:
   - `squash: true` => `on`
   - `squash: false` => `off`
   - otherwise default `auto`
6. Capture `base_hash`: `git -C {repo_root} rev-parse HEAD`.
7. Parse the `## Verification` section from `{plan_contents}`:
   - Locate the `## Verification` heading — a line matching exactly `## Verification` (two `#` followed by a space). Collect content until the next level-2 heading (a line starting with `## ` where the third character is a space, not another `#`) or end of file. Sub-headings like `###` within the section are included, not treated as boundaries.
   - Extract executable commands from:
     - **Fenced code blocks** tagged with `bash`, `sh`, `shell`, `zsh`, or with no language label. Trim leading whitespace from each line. Join backslash-continued lines (`\` at end of line) with the next line before processing. Each non-blank, non-comment line (after joining) is a command — no prefix validation needed since fenced blocks are unambiguously code. Fenced blocks with other language tags (e.g., `python`, `json`, `yaml`) are skipped.
     - **Inline backtick commands in list items**: backtick-delimited spans within numbered or bulleted list items. Trim leading/trailing whitespace from the extracted span. **First-word validation** (inline backticks only): extract the first whitespace-delimited word from the span (or the entire span if no whitespace). Accept the span only if the first word starts with `./` or matches one of these command words exactly: `cd`, `git`, `npm`, `npx`, `yarn`, `pnpm`, `gradle`, `gradlew`, `mvn`, `make`, `cargo`, `go`, `python`, `python3`, `pytest`, `docker`, `docker-compose`, `curl`, `grep`. Reject all other backtick spans (e.g., bare identifiers, prose fragments, or non-command words like `pythonVersion`).
   - **Working directory resolution** — process commands to determine `{cwd, command}` pairs:
     - **`cd && cmd`** (all sources — fenced blocks and inline backticks): `cd <dir> && <rest>` becomes `{cwd=<dir>, command=<rest>}`.
     - **Multi-line `cd` in fenced blocks**: a bare `cd <dir>` line (no `&&`) sets the working directory for all subsequent commands in that fenced block, until another `cd` line resets it. The `cd` line itself is not emitted as a command. Example: `cd libraries/catalog-utils` on line 1 sets cwd, `./gradlew test ...` and `./gradlew check` on lines 2-3 inherit that cwd.
     - **Bare `cd` in inline backticks**: a bare `cd <dir>` (no `&&`) from an inline backtick is discarded — standalone `cd` has no effect since shell state does not persist between Bash calls. Print a warning: `Warning: Discarding bare 'cd <dir>' — use 'cd <dir> && <command>' syntax instead.`
     - **No `cd` prefix**: the working directory is `{repo_root}`.
     - **Path resolution**: if `<dir>` is an absolute path, use it as-is. If relative, resolve as `{repo_root}/<dir>`. Canonicalize the result by collapsing all `.` and `..` segments to produce a clean absolute path. After canonicalizing, verify the path starts with `{repo_root}/` (or equals `{repo_root}`). If not, stop and report `Error: Verification cd target resolves outside repository root: <resolved_path>`.
   - Store as ordered list `{verification_commands}` (each entry: `{cwd, command}`).
   - If a `## Verification` heading exists but `{verification_commands}` is empty: **stop** and report `Error: Verification section found but no parseable commands extracted. Fix the plan or remove the section.`
   - If no `## Verification` heading exists at all: print `Warning: Plan has no Verification section. Verification will be skipped.` and proceed.

## Phase 2: Implement and Initial Commit

1. **Delegate implementation to sub-agent**:

   **Step 1a — Parse file list**: Extract a file list from `{plan_contents}` by looking for a `## Files to modify` heading or similar enumeration of file paths (e.g., bulleted/numbered lists of paths, or paths mentioned in section headings like `### path/to/file`). Store as `{expected_files}`. If no parseable file list is found, log `Warning: Plan has no parseable file list — completeness tracking will be limited to the implementer's self-report.` and set `{expected_files}` to empty. Store all paths as repo-relative (strip any leading `{repo_root}/` or `./` prefix). This ensures consistent comparison with `{changed_files}` in Step 1d.

   **Step 1a2 — Resolve project guidelines for implementer**: Resolve CLAUDE.md guidelines scoped to the expected files. Since the orchestrator builds Bash commands via string interpolation (not structured argv), each file path must be **single-quoted** to prevent all shell expansion (`$`, backticks, `$()`, globbing, word splitting):

   If `{expected_files}` is non-empty:

   ```bash
   python3 ~/.claude/scripts/resolve-claude-md.py \
     --git-dir {repo_root} \
     --merge-base {base_hash} \
     --files 'path/to/file1' 'path/to/file2' ... \
     --depth 5
   ```

   Each element of `{expected_files}` is passed as a separate single-quoted argument after `--files`. Single quotes block all shell expansion — no variable substitution, no command substitution, no globbing. Do NOT use double quotes (which allow `$()` expansion) or join paths into a single string. If a path contains a literal single quote, escape it using the POSIX idiom `'\''` (end quote, escaped quote, restart quote).

   If `{expected_files}` is empty, resolve with root-only scope (omit `--files`):

   ```bash
   python3 ~/.claude/scripts/resolve-claude-md.py \
     --git-dir {repo_root} \
     --merge-base {base_hash} \
     --depth 5
   ```

   **Error handling** — distinguish script failure from empty results:
   - If the script exits non-zero: **stop and report the error** (e.g., bad invocation, JSON parse failure, git error). Do not silently continue — this masks real bugs.
   - If the script exits 0 but `resolved_content` is empty: set `{implementer_guidelines}` to empty and log: `Guidelines: No CLAUDE.md files found for implementer scope.` This is normal for repos without CLAUDE.md.
   - If the script exits 0 and `resolved_content` is non-empty: store the text as `{implementer_guidelines}` and log: `Guidelines: Resolved for implementer ({N} chars).`

   **Step 1b — Launch implementer**: Launch a Task call with `subagent_type: implementer`, passing:
   - The repo root: `{repo_root}`
   - The full plan: `{plan_contents}`
   - Target file list: if `{expected_files}` is non-empty, include `Implement changes for these files: <list>`. If `{expected_files}` is empty, include `Implement all changes described in the plan.`
   - Include: `After implementing all files, perform the self-validation step described in your agent rules before writing your completion report.`
   - If `{implementer_guidelines}` is non-empty, include the following block:

   ```
   Project Guidelines (pre-resolved from CLAUDE.md files):
   ---BEGIN GUIDELINES---
   {implementer_guidelines}
   ---END GUIDELINES---

   Follow these project guidelines when implementing changes. They define project-specific conventions for naming, patterns, style, and structure.
   ```

   On retries, the target file list is always narrowed to remaining files only.
   On retries, the same `{implementer_guidelines}` block is included unchanged.

   **Step 1c — Invariant check** (run after EVERY implementer return, before any retry or continuation):
   - `git -C {repo_root} rev-parse HEAD` must equal `{base_hash}` (catches commits, resets, rebases)
   - `git -C {repo_root} diff --cached --name-only` must be empty (catches prohibited `git add`)

   Recovery depends on which check failed:
   - **HEAD moved**: run `git -C {repo_root} reset --hard {base_hash}` to restore the branch. Report untracked files via `git -C {repo_root} clean -dn`. Stop with error: `Implementer moved HEAD — branch has been reset to {base_hash}. Tracked file changes are lost. Untracked files may remain: <list>. Retry from scratch.`
   - **Index dirty (HEAD unchanged)**: run `git -C {repo_root} reset HEAD` to unstage. Working tree edits are preserved. Stop with error: `Implementer staged files — index has been cleaned, working tree edits preserved, review before retrying.`

   **Step 1d — Collect changed files**: Set `{changed_files}` to the union of:
   - `git -C {repo_root} diff --name-only` (modified tracked files)
   - `git -C {repo_root} ls-files --others --exclude-standard` (newly created untracked files)

   If `{expected_files}` is non-empty, compute `{unexpected_files}` = files in `{changed_files}` that are not in `{expected_files}`. All paths must be compared as repo-relative (`git diff --name-only` and `git ls-files --others --exclude-standard` already output repo-relative paths; `{expected_files}` must also be stored as repo-relative paths, stripped of any leading `./` or `{repo_root}/` prefix). If `{unexpected_files}` is non-empty, log a warning for each: `Warning: Implementer modified unexpected file: <file> — not in expected file list.` This is a warning, not a hard stop — legitimate cases include auto-generated files and import reorganization. Phase 2 step 3 ("sanity-check your own diff and remove accidental edits") acts on this information.

   **Step 1e — Parse completion report and handle retries**:
   - If the report is **missing or malformed** (no parseable `Status:` line, or `Files completed`/`Files remaining` not extractable): derive remaining files by taking `{expected_files}` NOT present in `{changed_files}`. Files in `{changed_files}` are treated as done. If `{expected_files}` is empty, cannot determine remaining — proceed to step 1f and rely on verification/review.
   - If `Status: complete`: cross-check by verifying every file in `{expected_files}` appears in `{changed_files}`. Any expected file missing is logged as a warning (`Warning: Implementer reports complete but <file> not in changed files — may be intentionally unchanged or missed`). Do NOT force a retry. Proceed to step 1f.
   - If `Status: partial` — continue to retry path below.
   - **Retry path**: launch another implementer Task for the remaining files only. The prompt explicitly lists only the `Files remaining` as the target. After each retry, run the invariant check (step 1c) and re-collect `{changed_files}` (step 1d) again. Repeat until all files are complete or 3 implementer attempts have been made.
   - If 3 attempts are exhausted with files still remaining — stop and report which files were not implemented.

   **Step 1f — Proceed**: Continue to step 2.

2. Confirm changes exist: `git -C {repo_root} status --porcelain` is non-empty.
3. Sanity-check your own diff and remove accidental edits.
4. **Verify**: If `{verification_commands}` is non-empty, run the verification procedure (defined in the Verification Procedure section below).
5. Stage specific files only:
   - use changed tracked files and untracked files from git output
   - never use `git add -A` or `git add .`
6. Ensure staged diff is non-empty.
7. Create commit with substantive subject + body:
   - subject:
     - with ticket: `{jira_ticket} <conventional-commit-subject>`
     - without ticket: `<conventional-commit-subject>`
   - body: minimum 3 non-empty lines describing what/why/how
8. Reject forbidden trailers:
   - `Co-Authored-By:`
   - `Generated with Claude Code`

## Phase 3: Review Loop (4 Agents, Parallel)

Set:

- `iteration = 0`
- `max_iterations = 7`
- `{last_review_hash} = null`
- `{last_iteration_issues} = null`
- `{active_reviewers}` = all 4 reviewers (bug-reviewer, architecture-reviewer, test-reviewer, code-quality-reviewer)
- `{degraded_consecutive}` = map of reviewer-name to consecutive-failure count (initialized to 0 for each)

Repeat until all reviewers approve or max iterations reached.

### 3.1 Run all reviewers in parallel

Resolve project guidelines via the CLAUDE.md resolution script (with caching):

If `iteration == 0`:
  - Run:
    ```bash
    python3 ~/.claude/scripts/resolve-claude-md.py \
      --git-dir {repo_root} \
      --merge-base {base_hash} \
      --ref-range {base_hash}..HEAD \
      --depth 5 \
      --check-head
    ```
  - Parse the JSON output and store:
    - `ancestor_dirs_list` — for reference in reviewer prompts
    - `expected_guidelines` — for cross-checking
    - `expected_directives` — for cross-checking (filter to depth<=2 for orchestrator expectations)
    - `pr_added_guidelines` — for CLI report
    - `warnings` — print each as `⚠ <warning text>`
    - `resolved_content` — to include in reviewer prompts
    - `guidelines_loaded_section` — to include in reviewer prompts
  - Store output as `{cached_guidelines_json}`.
  - Store the ancestor directory set as `{cached_ancestor_dirs}`.

If `iteration > 0`:
  - Compute new directories from remediation delta:
    `git -C {repo_root} diff --name-only {last_review_hash}..HEAD`
    Extract ancestor directories from these paths.
  - If all new ancestor directories are a subset of `{cached_ancestor_dirs}`:
    Reuse `{cached_guidelines_json}` — set `resolved_content`, `guidelines_loaded_section`,
    etc. from cache. Log: "Guidelines: reusing cached resolution (no new ancestor directories from remediation)."
  - If new directories appear:
    Re-run `resolve-claude-md.py` with the full range. Update `{cached_guidelines_json}`
    and `{cached_ancestor_dirs}`. Log: "Guidelines: re-resolving (N new ancestor directories from remediation)."

If the script exits non-zero, stop and report the error.
If `warnings` is non-empty, print all warnings but do not treat them as fatal.

If `iteration > 0` and `{last_iteration_issues}` is not null, construct a `Prior Iteration Context` block to append to each reviewer's prompt. Use the current `{last_review_hash}` (which still points to the previous iteration's HEAD) for STATUS determination — do NOT update it yet:

After constructing the Prior Iteration Context block (or skipping it for iteration 0), capture `{last_review_hash}`: set to `git -C {repo_root} rev-parse HEAD` immediately before launching reviewers. This captures the HEAD that reviewers will analyze. It persists unchanged until the next iteration's reviewer launch.

```
Prior Iteration Context (iteration {N-1}):
The following issues were raised in the previous review iteration:

[For each issue, up to 10 highest-priority:]
- [{P-tier}] {source-reviewer}: {issue-title} at {file}:{lines} — {STATUS}

STATUS is determined by the orchestrator (heuristic — reviewers should verify):
- LIKELY_FIXED: remediation commit (git diff {last_review_hash}..HEAD) touched the flagged
  file. This is a heuristic hint — the touched file may contain a nearby edit, formatting
  change, or incomplete fix. Reviewers MUST verify the original concern was actually resolved.
  If it was not, re-raise it.
- DEFERRED_P2: P2 or nitpick issue, intentionally not fixed — do NOT re-raise unless
  the relevant code has changed in a way that increases severity
- OPEN: P0/P1 that should have been fixed — verify the remediation was adequate

[If more than 10 issues: "Plus N additional P2/nitpick items, all deferred."]

Focus your review on:
1. Verifying OPEN items were adequately resolved
2. Verifying LIKELY_FIXED items — confirm the fix actually addresses the concern;
   re-raise if the fix is incomplete or cosmetic
3. NOT re-raising DEFERRED_P2 items unless code changes increased their severity
```

To determine STATUS for each prior issue:
- For P0/P1 issues: check if `git -C {repo_root} diff --name-only {last_review_hash}..HEAD` includes the flagged file. If yes, mark as LIKELY_FIXED; if no, mark as OPEN.
- For P2/nitpick issues: mark as DEFERRED_P2.

Run these subagents every iteration (from `{active_reviewers}`):

- `code-quality-reviewer`
- `architecture-reviewer`
- `test-reviewer`
- `bug-reviewer`

Launch all active (non-dropped) reviewers as parallel Task calls in a single message. Do NOT use `run_in_background` — foreground parallel calls already run concurrently and return all results in one turn.

CRITICAL: You MUST emit Task calls for ALL active reviewers in your NEXT single message — not 1 then the rest, not split across turns. When all 4 reviewers are active, this means 4 Task calls. If reviewers have been dropped (see Section 3.2 step 2 — parse failure handling), emit one Task call per remaining active reviewer. Pre-compute all reviewer prompt strings (diff context, guidelines, plan summary) before emitting any Task call. If your response contains fewer Task calls than the current active reviewer count, you have violated this rule.

After all reviewers return: if you detect (from your own message history) that reviewer Task calls were spread across more than one turn, log: "Warning: Reviewers launched across N turns instead of 1 — parallelism degraded."

Each reviewer prompt must include:

- compact plan summary from `{plan_contents}`
- commit context: `git -C {repo_root} log {base_hash}..HEAD`
- instruction to follow that reviewer's required output format exactly
- Prior Iteration Context block (if `iteration > 0` and `{last_iteration_issues}` is not null — see above)
- the following file-read, git instructions, and pre-resolved guidelines block:

```
IMPORTANT instructions for this review:
- All file reads must use absolute paths under {repo_root}/
- Your review scope is the diff range: {base_hash}..HEAD
- Use `git -C {repo_root} diff --no-renames {base_hash}..HEAD` to see the full diff
- Use `git -C {repo_root} diff --no-renames {base_hash}..HEAD -- <file>` for a single file's diff
- Use `git -C {repo_root} diff --no-renames --name-only {base_hash}..HEAD` for changed file list
- Use `git -C {repo_root} log {base_hash}..HEAD` for commit history
- Read files under {repo_root}/ to examine surrounding context beyond the diff

Project Guidelines (pre-resolved from CLAUDE.md files at merge-base {base_hash}):
---BEGIN GUIDELINES---
{resolved_content}
---END GUIDELINES---

For your #### Guidelines Loaded output section, use this pre-computed block:
---BEGIN GUIDELINES_LOADED---
{guidelines_loaded_section}
---END GUIDELINES_LOADED---
```

### 3.2 Parse, retry, and classify all issues

1. For each reviewer, attempt to parse:
   - Exactly one verdict: `### Verdict: APPROVE` or `### Verdict: REQUEST_CHANGES`
   - All issues from `REQUEST_CHANGES` verdicts, each tagged with **source reviewer identity** (bug-reviewer, architecture-reviewer, test-reviewer, code-quality-reviewer), title, file, lines, severity, confidence, category, problem, suggestion. Extract the `**Confidence**` field from each issue. If the field is missing (e.g., reviewer did not include it), default to `likely`.
   - Non-blocking blocks from `APPROVE` verdicts: `#### Nitpick N:` (architecture-reviewer, code-quality-reviewer) and `#### Recommendation N:` (test-reviewer), both with `**Comment**:` instead of `**Problem**:`/`**Suggestion**:`. Infer severity=nitpick from the header and include in classification. These do not need severity/category fields to parse successfully. Note: bug-reviewer does not emit nitpick blocks in APPROVE verdicts.
   - `#### Guidelines Loaded` section: extract guideline entries and directive sub-items separately.
     - **Guideline entries**: top-level bullets only — lines matching `- <path> (<source>)`
       with no leading indentation before the `-`. These are CLAUDE.md file paths.
     - **Directive sub-items**: indented bullets matching `  - @<directive> -> <resolved-path> (<status>)`
       (with 2+ spaces before `-`). These belong to the nearest preceding guideline entry.
     Cross-check guideline entries against `expected_guidelines` from script output:
     - Section missing entirely: log warning
       "Warning: <reviewer-name> did not report Guidelines Loaded — CLAUDE.md context unverifiable"
     - `expected_guidelines` is non-empty but reviewer reports "None found": log warning
       "Warning: <reviewer-name> reported no guidelines but expected: <expected list>"
     - Reviewer reports paths not in `expected_guidelines`: log warning
       "Warning: <reviewer-name> reported unexpected guideline path: <path>"
     - Expected path missing from reported set: log warning
       "Warning: <reviewer-name> did not report expected guideline: <path>"
     These are warnings only — do not trigger parse failure, retry, or verdict override.
   - For each entry in `expected_directives` (depth<=2 from script output), check if the reviewer
     reported a matching `@<directive>` sub-item under the corresponding parent path:
     - Directive not reported at all: log warning
       "Warning: <reviewer-name> did not report expected @ directive: @<directive> in <parent-path>"
     - Directive reported with any status (`resolved`, `truncated`, `not-found`,
       `cycle-skipped`): no warning (reviewer acknowledged the directive)
     - Directive reported with status `budget-dropped`: log informational
       "Info: <reviewer-name> budget-dropped @ directive: @<directive> in <parent-path>"
     - Reviewer reports @ directives not in `expected_directives`: no warning
   These are warnings only — do not trigger parse failure, retry, or verdict override.
   After completing all cross-checks, emit a compact guidelines summary to the CLI.
   Note: The implement command has no HTML report phase — this transient CLI summary is the
   sole guidelines visibility point, deliberately compact to avoid noise in the review loop.
   - If `expected_guidelines` is empty: `Guidelines: No CLAUDE.md files found in ancestor directories.`
   - Otherwise: `Guidelines: <N> CLAUDE.md files, <M> @ directives` (counts from `expected_guidelines` and `expected_directives`)
     - If all reviewers matched expectations: `  Reviewers: <N>/<active> matched` (where N = number of reviewers with parseable output, active = len({active_reviewers}) this iteration)
     - For any warnings: emit one `  ⚠ <reviewer-name>: <warning summary>` line per warning
   - If `pr_added_guidelines` is non-empty, append: `  PR-added (skipped): <path>, ...`
   **Dimensions breadth check** (all reviewers):
   - Extract `#### Dimensions Evaluated` from each reviewer.
   - Count: `{evaluated}` = OK + Issue count, `{total}` = expected dimensions for that reviewer.
   - Expected counts: bug=15, architecture=11, test=10, code-quality=8.
   - If `{evaluated}/{total}` < 0.50: log warning.
   - If section missing: log warning.
   - Warnings are informational only — no retry, no verdict override.

   Emit breadth warnings in the compact CLI summary alongside existing guidelines warnings:
   ```
   Breadth: <N>/<active> reviewers evaluated >=50% dimensions
     ⚠ <reviewer-name>: evaluated only {evaluated}/{total} dimensions
   ```
2. **Parse failure handling**:
   - If a reviewer's output is unparseable, rerun that reviewer once.
   - If still unparseable on retry:
     a. Log: "Warning: {reviewer-name} produced unparseable output after retry.
        Continuing with {N} of len({active_reviewers}) active reviewers."
     b. Mark that reviewer as `degraded` for this iteration. Increment
        `{degraded_consecutive}[reviewer-name]`.
     c. Proceed with classification using only successfully parsed reviewers.
   - If a reviewer parses successfully, reset `{degraded_consecutive}[reviewer-name]` to 0.
   - Minimum thresholds (both must be satisfied, checked against the original
     4 reviewers — not the dynamic active count):
     a. At least 2 reviewers produced parseable output this iteration.
     b. At least one P0-capable reviewer (bug-reviewer or architecture-reviewer)
        produced parseable output this iteration. If both P0-capable reviewers
        are degraded, stop and report — the loop cannot safely approve without
        correctness coverage.
     If either threshold is violated, stop and report which reviewers failed.
   - Special case: if bug-reviewer alone is degraded (architecture-reviewer is OK),
     log a prominent warning:
     "Warning: bug-reviewer degraded — correctness coverage reduced. architecture-reviewer
     provides partial P0 coverage for this iteration."
   - Re-attempt degraded reviewers on the next iteration (they may succeed on a
     simpler diff after remediation).
   - If a reviewer's `{degraded_consecutive}` count reaches 3, drop it from
     `{active_reviewers}` and log: "Warning: {reviewer-name} dropped after 3
     consecutive failures." The minimum thresholds above still apply after
     dropping — if dropping a reviewer would violate a threshold, stop instead
     of dropping.
3. Classify every successfully parsed issue into P0/P1/P2/nitpick. Classification rules are **scoped by source reviewer** — the reviewer that produced the issue determines which rule applies:
   - **P0**: bug-reviewer severity=high (any category), bug-reviewer severity=medium AND category in {security, data-integrity, race-condition}, architecture-reviewer severity=high
   - **P1**: bug-reviewer severity=medium (remaining), bug-reviewer severity=low AND category in {security, data-integrity}, architecture-reviewer severity=medium, test-reviewer severity=high, code-quality-reviewer severity=high
   - **P2**: bug-reviewer severity=low (remaining), architecture-reviewer severity=low, test-reviewer severity=medium or low, code-quality-reviewer severity=medium or low
   - **Nitpick**: any reviewer severity=nitpick
3b. **Confidence adjustment** (applied after severity classification):
   - If confidence=speculative: downgrade one tier (P0->P1, P1->P2, P2->Nitpick, Nitpick stays Nitpick)
   - If confidence=certain or likely: no adjustment
   - Log each downgrade: "Downgraded: {title} {from}->{to}, confidence=speculative"
4. Override the overall verdict:
   - `REQUEST_CHANGES` if any P0 or P1 issue exists
   - `APPROVE` otherwise (includes cases where dissenting reviewers only have P2 or nitpick-tier findings)
5. Store `{last_iteration_issues}`: set to the full classified issue list (all P-tiers) after parsing and classification completes. This is read when constructing the Prior Iteration Context block for the next iteration.

### 3.3 Decision

- If classified verdict is `APPROVE`: exit loop and finalize.
- If classified verdict is `REQUEST_CHANGES`:
  - Before implementing fixes, deduplicate P0/P1/P2 findings across reviewers:
    - If multiple reviewers flag the same file + line(s) range, merge into one fix item
      and note which reviewers flagged it (use `File` + `Line(s)` fields for matching; nitpick-tier items are excluded from deduplication)
  - Prioritize P0 > P1
  - Address P0 items first, then P1; these tiers must be resolved before the loop can reach `APPROVE`
  - P2 items: fix opportunistically alongside P0/P1 fixes in the same iteration, but do not force additional review cycles for P2-only findings
  - implement fixes
  - add/update regression tests when fixing bug/correctness findings (when feasible)
  - **run verification procedure** (same as Phase 2 step 4)
  - stage specific files only
  - if staged diff is non-empty, create a remediation commit with substantive body
  - increment `iteration` and continue to next review iteration

If `iteration >= max_iterations` and classified verdict is not `APPROVE`, stop and report unresolved issues.

## Verification Procedure

This procedure is referenced by Phase 2 step 4 and Phase 3.3.

Allows up to 3 fix-and-rerun cycles. The initial run is not counted as a fix attempt.

1. Run each command in `{verification_commands}` sequentially via Bash using its parsed `cwd`. Capture exit code and output (tail last ~50 lines on failure for context).
2. If all commands exit 0: print `Verification passed ({N} commands).` and return success.
3. If any command fails:
   - If 3 fixes have already been applied in this invocation: stop and report the failing command, its exit code, and output tail.
   - Otherwise: read the failure output, diagnose and fix the issue, and go to step 1 (re-run all commands from the start — a fix for one command could break another).
4. If a formatting command (e.g., `spotlessApply`) modifies files but exits 0, that is not a failure — proceed to the next command.

Each invocation of this procedure starts with a fresh fix count. Verification fix attempts do NOT increment the Phase 3 review `iteration` counter.

## Phase 4: Optional Squash and Finalize

1. Count commits since `base_hash`.
2. Squash policy:
   - `off`: keep commits
   - `auto`: squash only when commit count > 1
   - `on`: squash when commit count > 1
3. If squashing:
   a. Capture `{pre_squash_head}`: `git -C {repo_root} rev-parse HEAD`
   b. Create backup: `git -C {repo_root} tag implement-backup/{branch} HEAD`
      (If tag already exists from a prior run, delete it first and recreate.)
   c. `git -C {repo_root} reset --soft {base_hash}`
   d. Create one clean final commit describing final implementation (not review process)
   e. If commit succeeds: delete the backup tag:
      `git -C {repo_root} tag -d implement-backup/{branch}`
   f. If commit fails: restore the branch:
      `git -C {repo_root} reset --hard {pre_squash_head}`
      Report: "Squash commit failed. Branch restored to pre-squash state
      ({pre_squash_head}). Original commits preserved."
4. Show final commit: `git -C {repo_root} log -1 --pretty=full`.
5. Report final commit hash and subject.

## Error Handling

| Case | Action |
|---|---|
| Not in git repo | Stop and report error |
| Working tree not clean at start | Stop and ask user to commit/stash first |
| Plan path missing/invalid | Stop and report path |
| No plan selected | Stop |
| No changes after implementation | Stop with `No changes produced` |
| Staged diff empty | Stop with `Nothing staged` |
| Reviewer unparseable twice | Mark degraded, continue if minimum thresholds met (see 3.2 step 2); stop if thresholds violated |
| Squash commit fails | Restore branch to pre-squash HEAD, report error |
| Both P0-capable reviewers degraded | Stop and report — cannot safely approve without correctness coverage |
| Max review iterations reached | Stop and report unresolved issues |
| Git commit fails | Stop and report git output |
| Verification section exists but no parseable commands | Stop and report error |
| No verification section in plan | Warn and skip verification |
| Verification fails after 3 fix attempts | Stop and report failing command, exit code, and output |
| Implementer moved HEAD | `git reset --hard {base_hash}`, report untracked residue, stop |
| Implementer staged files (HEAD unchanged) | `git reset HEAD`, stop (working tree preserved) |
| 3 implementer attempts exhausted with remaining files | Stop and report unimplemented files |
| Implementer report malformed, expected files available | Derive remaining from diff, retry untouched files only |
| Implementer report malformed, no expected files | Warn, proceed (verification + review catch gaps) |
| Implementer reports complete but expected file has no diff | Warn (may be intentional), proceed |
| Implementer modified files outside expected list | Warn per file (step 3 sanity-check can revert) |

## Important Rules

- Never use `git add -A` or `git add .`.
- Always run git commands as `git -C {repo_root} ...` after preflight.
- Always run all active (non-dropped) reviewers each iteration.
- Keep review prompts compact and focused on `{base_hash}..HEAD`.
- Keep commits descriptive with real bodies.
- Never include forbidden trailers.
- Phase 2 implementation must be delegated to the `implementer` sub-agent — do not implement plan changes directly in the orchestrator context.
- Do not rewrite history unless squash mode resolves to `auto` or `on`, or when recovering from an implementer invariant violation (restoring `{base_hash}`).
- Before every commit (initial or remediation), run verification commands from the plan's ## Verification section. Never commit code that fails verification.
