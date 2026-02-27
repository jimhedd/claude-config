---
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Task, AskUserQuestion
description: "Refine an implementation plan using parallel correctness and completeness critics"
requires-argument: false
argument-hint: "plan-name.md [--new-file] [--single]"
argument-description: "Optional plan file. --single for 1 pass (default is 2). If omitted, choose from recent files in ~/.claude/plans"
---

# Refine Plan with Parallel Critics

Run two specialized critic agents (correctness and completeness) against a draft implementation plan, then revise the plan based on structured findings.

## Phase 0: Parse Arguments, Resolve Plan, and Load Guidelines

1. Parse `$ARGUMENTS` for flags:
   - `--new-file` => `new_file_mode=true` (write refined plan to `{stem}-refined.md`)
   - `--single` => `single_mode=true` (run 1 critic pass instead of default 2)
   - `--iterate` => accepted for backward compatibility; log: `Note: --iterate is now the default. Flag ignored.`
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

   **Step 7b — Plan-scoped directory probing**: Extract file paths from `{plan_contents}` by looking for a `## Files to modify` heading or similar enumeration of file paths (bulleted/numbered lists of paths, or paths mentioned in section headings like `### path/to/file`). Store as `{plan_files}`.

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
- 2 (default)
- 1 (with `--single`)

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

On pass 2, include additional context based on pass 1 results. These two cases are mutually exclusive:

**Case A — Pass 1 was clean (both ACCURATE + COMPLETE)**: Prepend the Adversarial Challenge prompt to each critic. Do NOT include Prior Iteration Context (there are no prior issues).

```
Adversarial Challenge:
The previous pass found zero issues. Your job on this pass is to find what was missed.

Specifically:
- Assume the first pass was superficial. Look deeper.
- Check claims and callers that are easy to overlook — indirect callers, re-exports,
  dynamic dispatch, string-based references.
- Verify edge cases in less-obvious code paths (error handlers, fallback logic, cleanup).
- Examine whether the plan's changes interact with each other in ways that create new issues.
- If you still find nothing after thorough investigation, return a clean verdict — but your
  evidence minimums are raised: correctness must verify ≥10 claims, completeness must trace ≥7 callers.
```

**Case B — Pass 1 had findings**: Include the standard Prior Iteration Context block. Do NOT include the Adversarial Challenge prompt.

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

**Evidence depth validation** (runs on every pass, not just clean verdicts):
- Parse `{N}` (claims verified) from the correctness critic's `#### Evidence` section and `{M}` (symbols traced) from the completeness critic's `#### Evidence` section.
- Standard minimums: correctness ≥8 claims, completeness ≥5 callers.
- Adversarial minimums (when the adversarial challenge prompt was active for this pass): correctness ≥10 claims, completeness ≥7 callers.
- If below threshold: log warning, re-run that critic once with explicit instruction to verify more claims / trace more symbols.
- Evidence-depth re-run and parse-failure retry (from Phase 1.2) share a single-retry budget per critic — max 1 total retry per critic regardless of reason.
- If a critic's retry budget is already spent and evidence minimums are still not met, log a warning (e.g., `Warning: <critic-name> evidence depth below threshold after retry budget exhausted`) and proceed as if evidence minimums were met.

**Pass outcome routing**:
1. If both verdicts are clean on pass 1 (2-pass mode): do NOT exit early. Continue to pass 2 with the adversarial challenge prompt (see Phase 1.1 Case A).
2. If both verdicts are clean on the final pass (pass 2, or pass 1 in `--single` mode): no findings to revise — proceed to Phase 1.5 (post-loop gate).
3. If verdicts have findings: proceed to Phase 1.4 (revision) as normal.

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
- [Skipped] {title} ({severity}): {brief justification for why this was not addressed}
```

Note: `[Skipped]` may only be used for should-fix and nice-to-fix items. Must-fix items cannot be skipped.

Write the revised plan to `{output_path}`.

Update `{plan_contents}` to the revised plan for the next pass.

Continue to next pass if applicable.

### 1.5 Post-loop gate

After all passes complete, scan the cumulative refinement log across all passes.

**Must-fix gate**: If any must-fix item was raised but not addressed in any pass's revision, **stop with error** listing the unresolved items. Must-fix items cannot be skipped.

**Should-fix gate**: Every should-fix item must be either:
1. Addressed in a revision (`[Should-fix]` in Changes list), OR
2. Explicitly skipped with justification (`[Skipped]` entry in refinement log)

Stop with error if any should-fix item is neither addressed nor justified-skipped.

## Phase 2: Report

Print a summary:

```
Plan refinement complete.
  Iterations: {pass}
  Correctness: {verdict} ({issues_found} issues found, {issues_fixed} fixed)
  Completeness: {verdict} ({gaps_found} gaps found, {gaps_addressed} addressed)
  Output: {output_path}
```

If both verdicts were clean on all passes:
```
Plan refinement complete.
  Iterations: {pass}
  Correctness: ACCURATE (0 issues, {N} claims verified across {pass} passes)
  Completeness: COMPLETE (0 gaps, {M} symbols traced across {pass} passes)
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
| Unresolved must-fix items after all passes | Stop with error listing unresolved must-fix items |
| Unjustified should-fix items after all passes | Stop with error listing should-fix items neither addressed nor skipped |

## Important Rules

- Always launch BOTH critics in parallel in a single message.
- The orchestrator revises the plan directly — do not spawn a third agent.
- Overwrite the plan file by default (plans are in git, `git diff` shows before/after). Use `--new-file` for safety.
- Default 2 iterations; use `--single` for 1 pass.
- Preserve the plan's existing structure when revising.
- Use distinct verdict names: `ACCURATE`/`HAS_ERRORS` (correctness) and `COMPLETE`/`HAS_GAPS` (completeness) — these are different from reviewer `APPROVE`/`REQUEST_CHANGES`.
