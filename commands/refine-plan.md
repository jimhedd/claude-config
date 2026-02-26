---
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Task, AskUserQuestion
description: "Refine an implementation plan using parallel correctness and completeness critics"
requires-argument: false
argument-hint: "plan-name.md [--new-file] [--iterate]"
argument-description: "Optional plan file. If omitted, choose from recent files in ~/.claude/plans"
---

# Refine Plan with Parallel Critics

Run two specialized critic agents (correctness and completeness) against a draft implementation plan, then revise the plan based on structured findings.

## Phase 0: Parse Arguments, Resolve Plan, and Load Guidelines

1. Parse `$ARGUMENTS` for flags:
   - `--new-file` => `new_file_mode=true` (write refined plan to `{stem}-refined.md`)
   - `--iterate` => `iterate_mode=true` (run 2 critic passes instead of 1)
   - Remaining argument (after flag removal) is the plan path

2. Resolve plan path from remaining argument:
   - absolute path => use as-is
   - relative path => resolve from current directory
   - bare `*.md` => also try `~/.claude/plans/<filename>`

3. If no plan argument:
   - list 5 most recent `~/.claude/plans/*.md`
   - ask user to choose via `AskUserQuestion`
   - if none selected, stop

4. Read selected plan into `{plan_contents}`. If file is missing or empty, stop with error.

5. Detect repo root: `git rev-parse --show-toplevel` (fallback: current working directory if not in a git repo). Store as `{repo_root}`. If not in a git repo, log: `Warning: Not in a git repo — critics will use filesystem only.`

6. Set `{output_path}`:
   - default: same file as input
   - with `--new-file`: `{stem}-refined.md` in the same directory as the input plan

7. **Resolve CLAUDE.md guidelines**:

   **Step 7a — Root resolution**: Run:
   ```bash
   python3 ~/.claude/scripts/resolve-claude-md.py \
     --git-dir {repo_root} \
     --working-tree \
     --depth 5
   ```
   Parse JSON output: store `{resolved_content}`, `{guidelines_loaded_section}`, `{expected_guidelines}`.

   **Step 7b — Plan-scoped directory probing**: Extract file paths from `{plan_contents}` by looking for a `## Files to modify` heading or similar enumeration of file paths (same parsing as `implement.md` Phase 2 Step 1a — bulleted/numbered lists of paths, or paths mentioned in section headings like `### path/to/file`). Store as `{plan_files}`.

   If `{plan_files}` is non-empty:
   - Compute ancestor directories from those paths (same algorithm as `compute_ancestor_dirs` in `resolve-claude-md.py:82` — split each path on `/`, collect all prefix directories, always include root).
   - For each ancestor dir, check if `{repo_root}/{dir}/CLAUDE.md` or `{repo_root}/{dir}/.claude/CLAUDE.md` exists on disk.
   - Read any found files and append their contents to `{resolved_content}`.
   - Add their paths to `{expected_guidelines}` and update `{guidelines_loaded_section}`.

   If `{plan_files}` is empty: root-only resolution is used. Log: `Warning: No parseable file list — guidelines probed from root only.`

   If the script fails: log `Warning: CLAUDE.md resolution failed — proceeding without guidelines.` and set `{resolved_content}` to empty, `{guidelines_loaded_section}` to "None found.", `{expected_guidelines}` to empty.

   **Known v1 limitation**: `@` directives in non-root CLAUDE.md files discovered via plan-scoped probing will not be recursively resolved.

## Phase 1: Critic Loop

Set `{max_passes}`:
- 1 (default)
- 2 (with `--iterate`)

Set `{pass} = 0`.

Repeat while `{pass} < {max_passes}`:

Increment `{pass}`.

### 1.1 Launch both critics in parallel

Launch both critic agents as parallel Task calls in a single message (mirrors the parallel reviewer pattern from `implement.md` Phase 3.1). Do NOT use `run_in_background` — foreground parallel calls already run concurrently and return all results in one turn.

CRITICAL: You MUST emit Task calls for BOTH critics in your NEXT single message — not 1 then the other, not split across turns. Pre-compute both prompt strings before emitting any Task call.

Each critic prompt must include:
- The full plan: `{plan_contents}`
- The repo root: `{repo_root}`
- Instruction to follow that critic's required output format exactly
- `"Be thorough and skeptical. Flag anything uncertain rather than assuming correctness/completeness. When in doubt, report it as a finding."`
- Pre-resolved CLAUDE.md guidelines (via markers):

```
Project Guidelines (pre-resolved from CLAUDE.md files):
---BEGIN GUIDELINES---
{resolved_content}
---END GUIDELINES---

For your #### Guidelines Loaded output section, use this pre-computed block:
---BEGIN GUIDELINES_LOADED---
{guidelines_loaded_section}
---END GUIDELINES_LOADED---
```

- If `{resolved_content}` is empty, omit the guidelines block entirely.

On pass 2 (with `--iterate`), also include Prior Iteration Context:

```
Prior Iteration Context (pass 1):
The following issues were raised in the previous critic pass:

[For each issue/gap:]
- [{severity}] {critic}: {title} — {STATUS}

STATUS:
- ADDRESSED: The plan was revised to fix this issue
- OPEN: The issue was not addressed in revision (verify if still applicable)

Focus on:
1. Verifying ADDRESSED items were adequately resolved
2. Finding any NEW issues introduced by revisions
3. NOT re-raising OPEN items unless they have worsened
```

The 2 critics to spawn (both via `Task` tool):

| Task | subagent_type | Focus |
|---|---|---|
| Correctness Critic | `plan-correctness-critic` | Factual claims: file paths, function names, API signatures, behaviors |
| Completeness Critic | `plan-completeness-critic` | Coverage gaps: callers, error handling, tests, conventions, sequencing |

### 1.2 Parse outputs and cross-check guidelines

For each critic, parse:
- Verdict: `### Verdict: ACCURATE` or `### Verdict: HAS_ERRORS` (correctness), `### Verdict: COMPLETE` or `### Verdict: HAS_GAPS` (completeness)
- Issues/gaps from the structured output
- `#### Guidelines Loaded` section

**Guidelines cross-check** (mirrors `implement.md` Phase 3.2 step 1):
- Extract guideline entries from each critic's `#### Guidelines Loaded` section.
- Compare against `{expected_guidelines}`.
- Log warnings for:
  - Section missing entirely: `Warning: <critic-name> did not report Guidelines Loaded`
  - Critic reports "None found" when guidelines exist: `Warning: <critic-name> reported no guidelines but expected: <list>`
  - Unexpected paths reported: `Warning: <critic-name> reported unexpected guideline path: <path>`
  - Expected paths missing: `Warning: <critic-name> did not report expected guideline: <path>`
- These are warnings only — do not trigger parse failure or retry.

**Parse failure handling**:
- If a critic's output is unparseable, rerun that critic once.
- If still unparseable on retry: log `Warning: <critic-name> produced unparseable output after retry.`
- If both critics are unparseable after retry: stop with error.
- If one is unparseable: warn and proceed with the other.

### 1.3 Classify findings

Classify each finding:
- **Must-fix**: severity=high
- **Should-fix**: severity=medium
- **Nice-to-fix**: severity=low

Deduplicate cross-critic findings targeting the same file/section **or the same claim topic**. If a correctness issue and a completeness gap both address the same concern (e.g., both flag a performance claim), merge them into a single fix item.

If both verdicts are clean (ACCURATE + COMPLETE), check evidence depth before exiting:
- Parse `{N}` (claims verified) from the correctness critic's `#### Evidence` section and `{M}` (symbols traced) from the completeness critic's `#### Evidence` section.
- Correctness critic must report at least 5 evidence items. If fewer: log warning, re-run that critic once with explicit instruction to verify more claims.
- Completeness critic must report at least 3 callers-checked evidence items. If fewer: log warning, re-run that critic once with explicit instruction to trace more symbols.
- Evidence-depth re-run and parse-failure retry (from Phase 1.2) share a single-retry budget per critic — max 1 total retry per critic regardless of reason.
- If a critic's retry budget is already spent and evidence minimums are still not met, log a warning (e.g., `Warning: <critic-name> evidence depth below threshold after retry budget exhausted`) and proceed as if evidence minimums were met.
- Only exit early if both critics meet their evidence minimums (or the above fallthrough applies). Skip to Phase 2 report.

### 1.4 Revise the plan

The orchestrator revises the plan directly (no separate agent).

For each finding, prioritized Must-fix first, then Should-fix, then Nice-to-fix:
1. Read the relevant code in the codebase (using Read, Grep, Glob as needed)
2. Update the plan section to fix the issue:
   - **Correctness issues**: Fix factual claims — correct file paths, function names, signatures, behaviors
   - **Completeness gaps**: Add missing steps, files, error handling, tests, imports, configuration
3. Preserve existing plan structure — do not reorganize sections unnecessarily

After all fixes, if the plan does not already contain a `## Refinement Log` section, append one. Then append a `### Pass {pass}` subsection under it:

```
## Refinement Log

### Pass {pass} — {date}

**Correctness**: {verdict} ({count} issues found, {count} fixed)
**Completeness**: {verdict} ({count} gaps found, {count} addressed)

Changes:
- [Must-fix] {title}: {brief description of what was changed}
- [Should-fix] {title}: {brief description of what was changed}
- [Nice-to-fix] {title}: {brief description of what was changed}
```

Write the revised plan to `{output_path}`.

Update `{plan_contents}` to the revised plan for the next pass (if `--iterate`).

Continue to next pass if applicable.

## Phase 2: Report

Print a summary:

```
Plan refinement complete.
  Iterations: {pass}
  Correctness: {verdict} ({issues_found} issues found, {issues_fixed} fixed)
  Completeness: {verdict} ({gaps_found} gaps found, {gaps_addressed} addressed)
  Output: {output_path}
```

If both verdicts were clean on the first pass:
```
Plan refinement complete.
  Iterations: 1
  Correctness: ACCURATE (0 issues, {N} claims verified)
  Completeness: COMPLETE (0 gaps, {M} symbols traced)
  Output: {output_path} (unchanged)
```

## Error Handling

| Case | Action |
|---|---|
| Plan file missing/empty | Stop with error |
| Both critics unparseable | Stop with error |
| One critic unparseable | Warn, proceed with other |
| Not in git repo | Warn, critics use filesystem only |
| Plan has no verifiable claims | Correctness critic may return HAS_ERRORS with low-severity unverifiable-claim issues — orchestrator treats these as Nice-to-fix; if all findings are unverifiable-claim issues only, log them in the refinement log but do not block the plan |
| CLAUDE.md resolution fails | Warn, proceed without guidelines |
| No parseable file list in plan | Warn, root-only guidelines probing |

## Important Rules

- Always launch BOTH critics in parallel in a single message.
- The orchestrator revises the plan directly — do not spawn a third agent.
- Overwrite the plan file by default (plans are in git, `git diff` shows before/after). Use `--new-file` for safety.
- Max 2 iterations (`--iterate`) — diminishing returns beyond that.
- Preserve the plan's existing structure when revising.
- Use distinct verdict names: `ACCURATE`/`HAS_ERRORS` (correctness) and `COMPLETE`/`HAS_GAPS` (completeness) — these are different from reviewer `APPROVE`/`REQUEST_CHANGES`.
