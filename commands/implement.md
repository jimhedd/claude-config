---
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Task, AskUserQuestion
description: "Implement a plan from a .md file (example: /implement my-plan.md), with review loop and auto-commit"
requires-argument: false
argument-hint: "plan-name.md"
argument-description: "Optional plan file for non-interactive execution; simplest usage is just the plan filename (for example: my-plan.md)"
---

# Implement with Review Loop

Orchestrate a **code → verify → commit → review → fix → ... → finalize** loop from a plan file. Verification is delegated to dedicated verification agents, and four reviewer agents run in parallel each iteration. The loop continues until all four approve, optionally applies safe non-blocking polish, then finalizes.

Quick usage:
- `/implement my-plan.md` (looks for `/Users/jim.he/.claude/plans/my-plan.md` when a bare filename is provided)
- `/implement /absolute/path/to/my-plan.md`
- `/implement my-plan.md --squash` (force final squash when more than one commit is produced)
- `/implement --no-squash my-plan.md` (preserve review/fix commits)

## Loop Model

Use two separate loops with different responsibilities:

1. `Review loop` (outer loop): max 5 iterations. Owns reviewer fan-out, feedback triage, remediation commits, and approval gating.
2. `Verification loop` (inner reusable loop): max 3 fix attempts per invocation. Owns delegated verification execution and verification-driven fixes.

Invoke the verification loop every time code changes and before any commit/review continuation:

- After initial implementation in Phase 2
- After reviewer-driven fixes in Phase 3

## Phase 0: Preflight Safety

1. Verify you are inside a git repository (`git rev-parse --is-inside-work-tree`)
   - If not in a git repo, report the error and **stop**
2. Record `repo_root` via `git rev-parse --show-toplevel`
   - Use `git -C {repo_root} ...` for all subsequent git commands in this run
3. Check working tree cleanliness with `git -C {repo_root} status --porcelain`
   - If output is non-empty, report "Working tree is not clean; commit or stash existing changes before /implement." and **stop**
4. Record current branch name (`git -C {repo_root} rev-parse --abbrev-ref HEAD`) for later commit formatting
5. Attempt to extract a JIRA ticket from branch name using regex `[A-Z][A-Z0-9]+-[0-9]+`
   - Store as `{jira_ticket}` if found
   - If not found, leave `{jira_ticket}` empty and continue (do not fail)
6. Initialize persistent run artifacts:
   - Build `implement_run_id` as `<UTC timestamp>-<repo name>-<short HEAD>`
   - Set `artifacts_root=/tmp/implement-runs/{implement_run_id}`
   - Validate `artifacts_root` before any writes:
     - must be non-empty
     - must be an absolute path under `/tmp/implement-runs/`
     - if validation fails, report and **stop** (do not attempt fallback writes)
   - Create directories:
     - `{artifacts_root}/verification`
     - `{artifacts_root}/reviews`
     - `{artifacts_root}/commits`
   - Write `{artifacts_root}/run_context.json` with at least:
     - `implement_run_id`, `repo_root`, `branch`, `jira_ticket`, `started_at_utc`

## Phase 1: Load Plan

1. Parse optional squash controls from `$ARGUMENTS` before resolving a plan path:
   - `--squash` => set `cli_squash_mode=on`
   - `--no-squash` => set `cli_squash_mode=off`
   - If both flags are present, report "Conflicting squash flags: use only one of --squash or --no-squash." and **stop**
   - Remove recognized squash flags from `$ARGUMENTS`; trim the remainder into `plan_arg`

2. **If `plan_arg` is provided** (non-empty):
   - Treat `plan_arg` as a single plan file path after squash-flag stripping
   - Resolve path in this order:
     - Expand `~` to the user home directory
     - If absolute path, use as-is
     - If relative path, resolve from the current working directory
     - If `plan_arg` is a bare filename ending in `.md`, also try `~/.claude/plans/<filename>`
   - Read the resolved path directly (no AskUserQuestion, no interactive plan picker)
   - If the file doesn't exist, report the resolved path error and **stop**

3. **If `plan_arg` is empty:**
   - List the 5 most recent `~/.claude/plans/*.md` files by modification time
   - If no plan files exist, report "No plan file found. Use plan mode first to create a plan." and **stop**
   - Read the first heading (the first line starting with `# `) from each file to use as a label
   - Use AskUserQuestion to present the plans as options — each option shows the heading and filename
   - If the user does not select a plan, report "No plan selected." and **stop**
   - Read the selected plan file contents

4. Determine plan-level squash preference from `{plan_contents}`:
   - If plan contains `squash: true`, set `plan_squash_mode=on`
   - If plan contains `squash: false`, set `plan_squash_mode=off`
   - Otherwise set `plan_squash_mode=unspecified`

5. Resolve final `squash_mode` with precedence:
   - `cli_squash_mode` (if provided) overrides `plan_squash_mode`
   - `plan_squash_mode` overrides default
   - default is `auto`
   - `auto` means: after all reviewers approve, squash to one commit only when there are multiple commits since `base_hash`

6. Display the selected plan contents and resolved `squash_mode`, then proceed directly to coding
   - Selecting the plan (path argument or AskUserQuestion choice) is the confirmation to execute
   - Persist `plan_path`, `squash_mode`, and `{plan_contents}` to:
     - `{artifacts_root}/run_context.json` (update existing JSON)
     - `{artifacts_root}/plan.md`

Store the plan file contents in a variable referred to as `{plan_contents}` for later phases.

## Phase 2: Code

1. Record `base_hash` via `git -C {repo_root} rev-parse HEAD` before making any changes
2. Implement the plan file by creating and editing files
3. Apply edit-safety checks before verification:
   - If you used any broad edit pattern (for example: replace-all, regex replacement, multi-file scripted rewrite), run `git -C {repo_root} diff --name-only` and inspect each touched file with targeted diff hunks before continuing
   - Revert any unintended hunks immediately, then re-check `git -C {repo_root} diff` to confirm only intended edits remain
4. Run `git -C {repo_root} status --porcelain` to verify changes were made (including untracked files) — if output is empty, report "No changes produced" and **stop**
5. Capture `verification_scope_files` snapshot before running verification:
   - Capture tracked changes: `git -C {repo_root} diff --name-only`
   - Capture untracked files: `git -C {repo_root} ls-files --others --exclude-standard`
   - Build immutable union (unique, normalized repo-relative paths) as `verification_scope_files`
   - Persist to `{artifacts_root}/verification/phase2-initial-scope-files.txt`
   - If the union is empty, report "No in-scope files detected for verification." and **stop**
6. Build `verification_commands` list once for this implement run:
   - Prefer commands specified in `{plan_contents}`
   - Parse the plan verification section into:
     - `verification_commands` (executable shell commands)
     - `verification_assertions` (non-command checks that must be validated deterministically, such as log content expectations or report/artifact existence)
   - Before finalizing command manifest, perform symbol preflight for newly introduced generated API types (for example protobuf/gRPC classes):
     - For each newly introduced generated-type import/name in changed files, confirm class naming/package resolution via existing repo usage, schema options (`java_outer_classname`/package/file-name conflict rules), or local generated artifacts when available
     - Do not rely on first compile failure as the primary discovery mechanism
   - If `{plan_contents}` specifies explicit verification commands, treat them as required source-of-truth gates:
     - run those commands as written (normalized only for `cwd`, no semantic substitution), except effectiveness-enforcement flags for required test gates described below
     - preserve all explicit commands in the manifest; only remove exact duplicates after normalization
     - do not silently replace a specified command with a broader/different command (for example `test` -> `check`)
     - if plan-specified targeted and aggregate gates both exist (for example `test --tests ...` and `check`), keep both and execute targeted gates first, aggregate gates last
     - if a specified command is impossible to run effectively in current context, stop and report for manual intervention unless user explicitly approves a fallback gate
   - Enforce required-command hygiene before execution:
     - hard-ban output-truncating wrappers in required gates (for example: `| tail`, `| head`, `| sed -n`, `| less`, `| more`)
     - hard-ban output-discard sinks in required gates (for example: `>/dev/null`, `1>/dev/null`, `2>/dev/null`, `&>/dev/null`)
     - if a required command violates these rules, stop and report the offending `command_id` + command text (do not continue)
   - Apply strict subsumption-based gate deduplication only to auto-detected supplemental commands (never to explicit plan-specified commands):
     - Goal: keep the strongest non-redundant gate set while preserving intent
     - Drop a command only when all conditions hold:
       - same toolchain and same scope/module
       - kept command is a strict superset signal of dropped command
       - dropping does not remove any unique required signal
     - Conservative examples that are safe to dedupe:
       - Gradle `:module:test` subsumes `:module:compileKotlin` / `:module:compileJava` / `:module:classes`
       - Gradle `test` subsumes `compileKotlin` / `compileJava` / `classes` in the same project scope
       - Gradle `check` subsumes `test`/`lint`/`typecheck` when those checks are part of that `check` task in the same scope
       - Maven `test` subsumes `compile` and `test-compile` for the same module
     - Never dedupe when coverage is uncertain; keep both commands
     - If plan text explicitly says a gate must run independently, do not dedupe it
   - If none are specified, auto-detect and include at least one meaningful gate:
     - tests first (preferred)
     - otherwise typecheck/lint/build
   - Optional fast pre-test compile gate for JVM/Kotlin projects:
     - If toolchain is Gradle/Maven + JVM/Kotlin and at least one required `test` gate exists, add one lightweight non-mutating compile/typecheck pre-gate before required tests
     - Prefer module-scoped `compileKotlin`; fallback to `compileJava` or `classes` (Gradle), or `-DskipTests compile` (Maven)
     - Set this pre-gate metadata as:
       - `gate_type=typecheck` (or `build` when only `classes/compile` is available)
       - `required=false` unless plan explicitly marks it required
       - `mutates_workspace=false`
       - `parallel_safe=false`
     - Schedule this pre-gate in the earliest read-only stage, before required test gates
   - Auto-added mutating commands policy:
     - prefer non-mutating verification commands first
     - add mutating commands (formatters/auto-fixers/code generators) only when explicitly required by `{plan_contents}` or when they are the only viable way to produce a meaningful verification gate
   - Minimize redundant auto-detected gates:
     - If an aggregate command already covers lower-level checks (for example, `check` covering test/lint), avoid adding duplicate commands unless explicitly required by the plan
   - Normalize commands to run from coordinator-provided `cwd`:
     - Do not prefix commands with `cd ... &&`; pass a clean command string
   - Assign execution metadata per command:
     - `stage`: integer stage index (`0`, `1`, ...)
     - `mutates_workspace`: `true` for formatters/auto-fixers/code generators; otherwise `false`
   - For each command, define:
     - `id`: stable short identifier
     - `command`: exact shell command
     - `source_command`: original command before any effectiveness-enforcement normalization (for audit output only)
     - `gate_type`: one of `test` | `typecheck` | `lint` | `build` | `format` | `custom`
     - `parallel_safe`: `true` only when command is independent and read-only; set `false` for integration/e2e/shared-state checks
     - `required`: `true` unless explicitly optional
     - `must_be_effective`: `true` for required `test` gates; otherwise `false` unless explicitly needed
     - `timeout_seconds`: optional when needed
   - Effective-execution enforcement for required test gates:
     - Goal: prevent false-positive PASS results from cache-only / up-to-date runs that execute zero tests
     - For Gradle required test gates (`must_be_effective=true`), append `--rerun-tasks` when not already present
     - Preserve the pre-normalized command as `source_command` in manifest output
     - If a toolchain has no safe deterministic rerun flag in current context, keep the command unchanged and rely on strict `gate_effective`/`tests_executed` checks
   - Default staging policy:
     - Stage `0`: all `mutates_workspace=true` commands (serial)
     - Stage `1`: all read-only verification gates (`mutates_workspace=false`) with `parallel_safe=true` when safe
     - If a read-only command is placed in stage `0` without an explicit dependency reason, normalize it to stage `1`
     - If optional JVM/Kotlin pre-test compile gate exists, place it in the earliest read-only stage and shift required test gates to the next read-only stage
     - If plan includes both targeted and aggregate read-only gates, schedule targeted gates in the earliest read-only stage and aggregate gates in the latest read-only stage
   - If all commands are mutating or non-parallel-safe, expect serial execution and do not force parallelism
   - Emit a deterministic `verification_manifest` before execution (for auditability):
     - Print one line per command with: `id`, `stage`, `gate_type`, `required`, `must_be_effective`, `parallel_safe`, `mutates_workspace`, `command`, `source_command` (when different)
     - Preserve this manifest and treat it as the source-of-truth command set for all subsequent verification-loop parsing
     - Also print `deduped_redundant_commands` (if any) with coverage rationale for each dropped command
     - Persist the machine-readable manifest to `{artifacts_root}/verification/phase2-initial-manifest.json`
     - Store this as `verification_manifest` for all later `RunVerificationLoop` invocations
     - Treat this manifest as immutable for the entire run:
       - do not add/remove/reorder commands in later review-loop verification passes
       - do not mutate command text/metadata outside allowed `source_command` effectiveness normalization already captured in this manifest
       - if later verification execution cannot run this manifest as-is, stop for manual intervention
7. If no verification command could be executed, report that and **stop** (do not commit)
8. Invoke `RunVerificationLoop(verification_commands, verification_manifest, context_label="phase2-initial")`
   - `RunVerificationLoop` is the only allowed entry point for verification orchestration in all phases; do not call `verification-coordinator` directly from Phase 2/3 flow
   - If return is `FAIL_MAX_ATTEMPTS`, report compact failure summary + log paths and **stop** (do not commit)
   - If return is `ERROR`, report and **stop** for manual intervention
   - If return is `PASS`, display a compact per-command verification table (`id`, `status`, `attempts`, `gate_effective`, `tests_executed`, `log_path`)
   - If return has any other status/value, treat it as protocol violation and **stop** for manual intervention
   - Persist latest verification summary JSON + table rows to:
     - `{artifacts_root}/verification/phase2-initial-summary.json`
     - `{artifacts_root}/verification/phase2-initial-summary.md`
   - Invoke `AuditRunArtifacts(required_paths)` for:
     - `{artifacts_root}/verification/phase2-initial-summary.json`
     - `{artifacts_root}/verification/phase2-initial-summary.md`
     - `{artifacts_root}/verification/phase2-initial-manifest.json`
     - If return is `ERROR`, report missing artifact paths and **stop**
   - Then invoke `ValidateVerificationAssertions(verification_assertions, verification_results, repo_root)`:
     - If return is `PASS`, continue
     - If return is `FAIL` or `ERROR`, report compact assertion failure summary and **stop**
   - Then invoke `ValidatePlanConformanceChecklist(plan_contents, repo_root, diff_range="{base_hash}..HEAD")`:
     - Persist outputs to:
       - `{artifacts_root}/verification/phase2-plan-conformance.json`
       - `{artifacts_root}/verification/phase2-plan-conformance.md`
     - Invoke `AuditRunArtifacts(required_paths)` for persisted conformance files; on `ERROR`, stop for manual intervention
     - If return is `FAIL` or `ERROR`, report compact unmet-requirements summary and **stop**
   - Never reinterpret verification or conformance failures as effective pass (for example “pre-existing/out-of-scope but proceed anyway”); fail closed and stop
9. Stage the relevant files with `git -C {repo_root} add <specific files>` — NEVER use `git add -A` or `git add .`
10. If staged diff is empty, report "Nothing staged" and **stop**
11. Create a commit with a detailed message:
   - **Subject line**:
     - If `{jira_ticket}` is present: JIRA ticket prefix followed by conventional commit format (e.g. `CAT-000 feat(scope): description`)
     - If `{jira_ticket}` is empty: use conventional commit format only (do not invent a ticket)
   - **Body**: spell out all goals and implementation details — what is being changed, why, and how. Include enough context that a reviewer reading only the commit message understands the full intent and scope of the change. Reference the plan file contents for context.
   - Build commit message explicitly as subject + body (for example via separate `-m` flags) so the body cannot be omitted accidentally
   - Body must contain concrete "what/why/how" details; minimum 3 non-empty lines
   - No Co-Authored-By tags or "Generated with Claude Code" signatures
12. Validate commit message completeness:
   - Run `git -C {repo_root} log -1 --pretty=%B`
   - If the commit body is missing or trivial, report and **stop** for manual intervention before entering review loop
   - Enforce forbidden commit trailers:
     - Reject commit messages containing any line that starts with:
       - `Co-Authored-By:`
       - `Generated with Claude Code`
     - If found, report the exact trailer lines and **stop** for manual intervention
   - Persist the created commit metadata to `{artifacts_root}/commits/phase2-initial-commit.json` (`hash`, `subject`, `body`)
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/commits/phase2-initial-commit.json"])`; on `ERROR`, stop for manual intervention

## Shared Procedure: RunVerificationLoop

Use this exact procedure in both Phase 2 and Phase 3.

Inputs:

- `verification_commands`
- `context_label` (for example: `phase2-initial`, `review-iteration-2`)
- `repo_root`
- `verification_manifest` (the immutable manifest persisted at `{artifacts_root}/verification/phase2-initial-manifest.json`)

Outputs:

- `status`: `PASS` | `FAIL_MAX_ATTEMPTS` | `ERROR`
- `verification_results`: parsed coordinator JSON summary object from the latest attempt (required when `status=PASS`)

Algorithm:

1. Set `verification_fix_attempt = 0` and `max_verification_fix_attempts = 3`
2. Enforce immutable command set before execution:
   - validate `verification_commands` exactly matches `verification_manifest.commands` for all entries by:
     - same list length and order
     - same `id`, `command`, `stage`, `gate_type`, `required`, `must_be_effective`, `parallel_safe`, `mutates_workspace`
   - if any mismatch is detected, return `ERROR` (command drift/manual intervention required)
3. Capture immutable `verification_scope_files` snapshot:
   - tracked: `git -C {repo_root} diff --name-only`
   - untracked: `git -C {repo_root} ls-files --others --exclude-standard`
   - union (unique, normalized repo-relative paths)
   - persist to `{artifacts_root}/verification/{context_label}-scope-files.txt`
   - if the union is empty, return `ERROR` (cannot enforce in-scope boundaries)
   - If a gate fails only on files outside this snapshot, treat as out-of-scope/pre-existing failure and return `ERROR` (manual intervention)
4. Repeat until return:
   - Never execute verification commands directly in the orchestrator via `Bash`; coordinator delegation is mandatory for all gates
   - Never run ad-hoc baseline probes (for example `git stash && <verification command> | tail ...`) to reclassify failures; out-of-scope/pre-existing suspicion is an immediate `ERROR` return
   - Invoke `Task` with `subagent_type=verification-coordinator` and wait synchronously for terminal output
   - Provide:
     - `cwd`: repo root
     - `run_id`: `{context_label}-attempt-{verification_fix_attempt}`
     - `flaky_retry_limit`: `1`
     - `commands`: `verification_commands`
   - Instruct coordinator to:
     - fan out to `verification-worker` tasks
     - execute commands by `stage` order
     - within each stage, run `mutates_workspace=true` commands serially
     - within each stage, run `mutates_workspace=false` and `parallel_safe=true` commands in parallel
     - within each stage, run `mutates_workspace=false` and `parallel_safe=false` commands serially
     - wait for all worker tasks to reach a terminal state before returning a verdict (no early return while workers are still running)
     - return gate effectiveness metadata per command (`gate_effective`, `tests_executed`, `ineffective_reason`) and worker drain counters (`workers_spawned`, `workers_completed`, `workers_inflight`)
     - write full command logs to files and return only compact JSON summary + log paths
   - Persist coordinator outputs per attempt:
     - Raw coordinator text: `{artifacts_root}/verification/{context_label}-attempt-{verification_fix_attempt}.txt`
     - Parsed JSON summary: `{artifacts_root}/verification/{context_label}-attempt-{verification_fix_attempt}.json`
   - Invoke `AuditRunArtifacts(required_paths)` for attempt artifacts above; if return is `ERROR`, return `ERROR`
   - If the Task tool returns a background/stream handle instead of terminal output, keep polling task output until terminal JSON is available
   - Require coordinator terminal output to contain exactly one `## Verification: Summary` section and one parseable JSON object
   - Never classify verification as PASS/FAIL until terminal coordinator output is received
   - If coordinator fails, times out, or returns unparseable output, re-run coordinator once immediately
   - If coordinator still fails/unparseable after retry, return `ERROR`
   - Parse coordinator output:
     - If `overall_status` is not exactly one of `PASS`, `FAIL`, `ERROR`, treat as `ERROR`
     - If worker drain invariants are violated (`workers_inflight > 0`, `workers_completed != workers_spawned`, missing required result entries, malformed effectiveness fields for `must_be_effective=true`, `commands_total != len(verification_commands)`, or `results.length != len(verification_commands)`), treat as `ERROR`
     - If `command_manifest_validated != true`, treat as `ERROR`
     - Validate command-manifest fidelity against `verification_manifest`:
       - each input `command_id` appears exactly once in `results`
       - each result `command` exactly matches the source manifest command string for that `command_id`
       - no extra result entries beyond the source manifest
       - if any mismatch is found, treat as `ERROR` (possible command substitution/drift)
     - If any `results[*].status` is not exactly one of `PASS`, `FAIL`, `ERROR`, treat as `ERROR`
     - If `overall_status=PASS` and any required-failure list is non-empty (`failed_required_ids` or `failed_ineffective_required_ids`), treat as `ERROR`
     - Independently enforce required test-gate effectiveness from `results`:
       - For each `required=true` and `gate_type=test` result, require `status=PASS`, `gate_effective=true`, and `tests_executed > 0`
       - If any required test result violates this, treat as `ERROR` even if coordinator reported `overall_status=PASS`
     - If any required command result is non-PASS, do not downgrade/relabel it (for example as `FAIL_PREEXISTING`) and do not continue this run; follow the `FAIL`/`ERROR` return paths below
     - If `overall_status=PASS`, return `PASS` with `verification_results`
     - If `overall_status=ERROR`, return `ERROR`
     - If `overall_status=FAIL` and `verification_fix_attempt >= max_verification_fix_attempts`, return `FAIL_MAX_ATTEMPTS`
     - If `overall_status=FAIL` and `verification_fix_attempt < max_verification_fix_attempts`:
       - surface `short_failure_digest` + `log_path` list to the main flow
       - do not replace or bypass explicit plan-specified verification commands during fixes
       - do not auto-fix failures in out-of-scope files captured outside `verification_scope_files`; report manual intervention instead
       - make focused fixes without weakening test intent:
         - do not remove/relax assertions, skip failing cases, add ignore allowlists, or reclassify failures as non-blocking solely to make gates pass
         - if failure indicates source-of-truth drift, prefer fixing the drift at the source; if not feasible in scope, stop and report for manual intervention
         - if a required test gate is ineffective (`gate_effective=false`, `tests_executed=0`, or equivalent), treat as verification failure requiring manual intervention unless user explicitly approves fallback
       - after each fix, recompute tracked+untracked union (`git diff --name-only` + `git ls-files --others --exclude-standard`);
         if new files appear outside immutable `verification_scope_files`, return `ERROR` and report those files as out-of-scope edits
       - increment `verification_fix_attempt` and continue loop
5. Never proceed while any required verification command is failing

## Shared Procedure: ValidateVerificationAssertions

Inputs:

- `verification_assertions`
- `verification_results` (from the terminal JSON summary returned by `RunVerificationLoop`)
- `repo_root`

Algorithm:

1. If `verification_assertions` is empty, return `PASS`.
2. For each assertion, build deterministic checks using only local artifacts:
   - `log_contains`: required substring/regex must be found in a referenced command `log_path`
   - `log_not_contains`: forbidden substring/regex must not be found in a referenced command `log_path`
   - `file_exists` / `glob_exists`: required report/artifact path exists under `repo_root`
3. Execute checks with concise Bash probes and collect pass/fail evidence per assertion id.
4. If an assertion cannot be mapped to a deterministic check, return `ERROR` (manual intervention required; do not silently ignore).
5. Return:
   - `PASS` when all assertions pass
   - `FAIL` when any mapped assertion fails
   - `ERROR` when assertion mapping/check execution is not reliable
6. Always print a compact assertion summary table (`id`, `status`, `evidence`).

## Shared Procedure: AuditRunArtifacts

Inputs:

- `required_paths`: list of absolute paths that must exist after a persistence step

Algorithm:

1. For each path in `required_paths`, verify the file exists.
2. For summary/report artifacts (`*.json`, `*.md`, `*.txt`), require non-empty content (`size > 0`) unless explicitly marked optional.
3. If any required path is missing or empty, return `ERROR` with a compact missing-path list.
4. Return `PASS` when all required paths are present and valid.

## Shared Procedure: ValidatePlanConformanceChecklist

Inputs:

- `plan_contents`
- `repo_root`
- `diff_range` (typically `{base_hash}..HEAD`)

Algorithm:

1. Extract explicit implementation requirements from the plan (numbered steps, normative bullets, and stated invariants).
2. Assign deterministic requirement ids (`REQ-001`, `REQ-002`, ...).
3. For each requirement, map to at least one implementation anchor in the diff range (`path:line`).
4. If a requirement references a named file/symbol that is not present in the current tree but can be deterministically resolved from git history, allow a historical anchor (`history:<commit>:<path>:line`) plus current diff/test anchors.
5. For behavior/correctness requirements (validation, dedup/grouping, ordering/mapping, error classification, request/response contracts), also require at least one test anchor (`path:line` + test name) in the diff range, unless infeasible reason is explicitly documented.
6. Produce a checklist with columns: `requirement_id`, `requirement`, `impl_anchor`, `test_anchor`, `status`, `notes`.
7. Persist checklist to:
   - `{artifacts_root}/verification/phase2-plan-conformance.json`
   - `{artifacts_root}/verification/phase2-plan-conformance.md`
8. If any explicit requirement is unmapped or weakly evidenced, return `FAIL`.
9. If extraction/mapping cannot be done deterministically, return `ERROR`.
10. Otherwise return `PASS`.
11. Never override `FAIL`/`ERROR` by narrative judgement; checklist result is authoritative for gating.

## Phase 3: Review Loop

Set iteration = 0, max_iterations = 5.

### Loop Start

1. Increment iteration
2. If iteration > max_iterations: **stop** — report remaining issues, leave commits as-is (not squashed)
3. Report: `"Review iteration {iteration}/{max_iterations}"`
3.5 Determine adaptive `review_depth` for this iteration using `{base_hash}..HEAD`:
   - Compute `changed_files` and total `changed_lines` from diff stats
   - Default `review_depth=focused`
   - Set `review_depth=deep` when any trigger is true:
     - `changed_files > 8`
     - `changed_lines > 400`
     - high-risk signals present (auth/permissions, money/data writes, concurrency/retries, schema/proto/API contract shifts, migrations)

4. Spawn ALL 4 reviewer agents **in parallel** using the Task tool. Each Task call must:
   - Use the appropriate `subagent_type` for the reviewer
   - Include this prompt structure:

   ```
   Review the git changes for the following plan:

   {plan_contents}

   Review depth for this iteration: `{review_depth}`.
   - If `review_depth=focused`, prioritize changed hunks plus immediate callers/callees/tests; avoid broad repo exploration unless needed to justify a finding.
   - If `review_depth=deep`, perform broader module-level tracing as needed.

   Run `git -C {repo_root} log {base_hash}..HEAD` to read the commit messages for context,
   then `git -C {repo_root} diff {base_hash}..HEAD` to see all changes. Perform your review strictly on this range.
   Only return REQUEST_CHANGES for concrete, evidence-backed issues with specific file/line references.
   For APPROVE verdicts, include an `Evidence` section with:
   - `Files reviewed:` listing files in the diff range that you inspected
   - At least two `Evidence N:` bullets with concrete checks anchored to `path:line`
   - At least one `Evidence N:` bullet anchored to a line added/modified in `{base_hash}..HEAD`, with a risk-specific rationale (not a generic style observation)
   If newly introduced or renamed callable symbols are present in the diff, code-quality and architecture reviews must include at least one explicit naming/API clarity evidence item for those symbols.
   If function signatures, caller/callee responsibilities, validation boundaries, side effects, or other contracts changed, the architecture reviewer must include a `#### Caller Impact` section with:
   - `Changed callable:` item(s) anchored to callable declaration/definition `path:line`
   - and either:
     - `Caller evidence N:` item(s) anchored to caller site `path:line` with compatibility assessment, or
     - explicit `No in-repo callers found` justification (only when true).
   For any blocking bug/correctness issue, explicitly include the overlooked edge case and the regression test that should cover it.
   Explicitly flag any test-dilution pattern (removed assertions, weaker checks, drift allowlists, ignored fields/cases) as REQUEST_CHANGES unless behavior/spec changed and is documented.
   If function signatures, caller/callee responsibilities, validation boundaries, or other contracts changed, the test reviewer must either:
   - cite specific existing tests (`path:line` + test name) that cover the shifted contract, or
   - return REQUEST_CHANGES with the exact regression test to add.
   If diff changes grouping/dedup/index remap/row-mapping logic, test and bug reviewers must explicitly verify ordering and mapping invariants with `path:line` evidence.
   If concern is uncertain or preference-only, classify it as a nitpick.
   Return your verdict in the structured format specified in your agent instructions.
   ```

5. Wait for all 4 reviewers to complete
   - Create the iteration directory first (`{artifacts_root}/reviews/iteration-{iteration}`)
   - Persist each reviewer raw output verbatim from Task output to `{artifacts_root}/reviews/iteration-{iteration}/<reviewer>-attempt-1.md` (do not synthesize or rewrite content)
   - Invoke `AuditRunArtifacts(required_paths)` for:
     - `{artifacts_root}/reviews/iteration-{iteration}/code-quality-reviewer-attempt-1.md`
     - `{artifacts_root}/reviews/iteration-{iteration}/architecture-reviewer-attempt-1.md`
     - `{artifacts_root}/reviews/iteration-{iteration}/test-reviewer-attempt-1.md`
     - `{artifacts_root}/reviews/iteration-{iteration}/bug-reviewer-attempt-1.md`
     - If return is `ERROR`, stop for manual intervention

6. Parse each reviewer's result for `Verdict: APPROVE` or `Verdict: REQUEST_CHANGES`, plus evidence completeness
   - If a reviewer fails, times out, or returns unparseable output, re-run that same reviewer once immediately
   - Persist retry output to `{artifacts_root}/reviews/iteration-{iteration}/<reviewer>-attempt-2.md` when retry occurs
   - Persist parse outcomes to `{artifacts_root}/reviews/iteration-{iteration}/<reviewer>-verdict.json`
   - Invoke `AuditRunArtifacts(required_paths)` for:
     - `{artifacts_root}/reviews/iteration-{iteration}/code-quality-reviewer-verdict.json`
     - `{artifacts_root}/reviews/iteration-{iteration}/architecture-reviewer-verdict.json`
     - `{artifacts_root}/reviews/iteration-{iteration}/test-reviewer-verdict.json`
     - `{artifacts_root}/reviews/iteration-{iteration}/bug-reviewer-verdict.json`
     - If return is `ERROR`, stop for manual intervention
   - Treat APPROVE output as unparseable unless it contains:
     - first non-empty line `## Review: ...`
     - exactly one `### Verdict: APPROVE` or `### Verdict: REQUEST_CHANGES`
     - `#### Evidence`
     - `Files reviewed:` with at least one repo-relative file path from the reviewed range (bare filenames are invalid)
     - at least two `Evidence N:` entries that include concrete `path:line` anchors
     - at least one `Evidence N:` entry anchored to a line that changed in `{base_hash}..HEAD`
   - Treat output as unparseable if issue/nitpick/recommendation file references are not repo-relative paths
   - Detect whether contract shifts are present in `{base_hash}..HEAD` (for example: changed function signatures, moved validation/filtering responsibility, changed preconditions, or caller/callee contract shifts)
   - If contract shifts are present, treat `architecture-reviewer` APPROVE as unparseable unless it includes `#### Caller Impact` with:
     - at least one `Changed callable:` evidence anchor (`path:line`), and
     - either at least one `Caller evidence N:` caller-site anchor (`path:line`) with compatibility rationale, or explicit `No in-repo callers found` justification
   - If contract shifts are present, treat `test-reviewer` APPROVE as unparseable unless at least one evidence item cites an existing covering test with `path:line` and test name
   - If it still fails/unparseable after retry, report "Reviewer <name> failed twice; stopping for manual intervention." and **stop**

7. Display a summary table:
   ```
   Code Quality:  ✓ APPROVED  (or ✗ CHANGES REQUESTED)
   Architecture:  ✓ APPROVED  (or ✗ CHANGES REQUESTED)
   Test Coverage: ✓ APPROVED  (or ✗ CHANGES REQUESTED)
   Bug Review:    ✓ APPROVED  (or ✗ CHANGES REQUESTED)
   ```

8. If ALL four return APPROVE:
   - Collect non-blocking reviewer feedback (`Nitpick` / `Recommendation`) across all reviewers
   - If there are no non-blocking items, break to Phase 4
   - If non-blocking items exist, run at most one optional polish pass in this iteration only when at least two reviewers independently raise the same actionable non-blocking item:
     - If no convergent non-blocking item exists, report the skipped polish items and break to Phase 4
     - Apply only clear, low-risk, behavior-preserving improvements (for example comments/docs accuracy, private symbol visibility, tiny local refactors, test readability)
     - Do not run automatic polish for public/external API renames without explicit user approval
     - Do not change intended behavior/spec in polish pass
     - Invoke `RunVerificationLoop(verification_commands, verification_manifest, context_label="review-polish-{iteration}")`
     - If return is `PASS`, invoke `ValidateVerificationAssertions(verification_assertions, verification_results, repo_root)`:
       - If assertion validation returns `FAIL` or `ERROR`, report and **stop**
     - If return is `FAIL_MAX_ATTEMPTS`, report compact failure summary + log paths and **stop**
     - If return is `ERROR`, report and **stop** for manual intervention
     - Stage changed files with `git -C {repo_root} add <specific files>` (never `git add -A` / `git add .`)
     - If staged diff is empty, report "No safe polish changes applied; proceeding to finalize." and break to Phase 4
     - Create a new commit (not amend) describing the polish changes with a substantive body
     - Enforce forbidden commit trailer policy (same checks as Phase 2 step 12); stop on violation
     - Persist polish commit metadata to `{artifacts_root}/commits/review-polish-{iteration}.json`
     - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/commits/review-polish-{iteration}.json"])`; on `ERROR`, stop for manual intervention
     - Go back to Loop Start (re-run all 4 reviewers on cumulative diff)

9. If ANY return REQUEST_CHANGES:
   - Compile all feedback from all reviewers into a unified issue list
   - De-duplicate overlapping issues and prioritize high → medium → low
   - Ignore nitpicks for gating purposes
   - Address each issue: read context, make the fix, verify the fix
   - For each blocking bug/correctness issue, add or update an automated regression test that covers the previously missed edge case
   - For any remediation that changes functional behavior (validation/parsing/grouping/mapping/error handling), add or update an automated regression test in the same iteration
   - Prefer writing the regression test before the code fix; at minimum, ensure the new test would fail before the fix and pass after the fix
   - If a regression test is not feasible, document the reason in the remediation commit message
   - Invoke `RunVerificationLoop(verification_commands, verification_manifest, context_label="review-iteration-{iteration}")`
   - If return is `PASS`, invoke `ValidateVerificationAssertions(verification_assertions, verification_results, repo_root)`:
     - If assertion validation returns `FAIL` or `ERROR`, report and **stop**
   - If return is `FAIL_MAX_ATTEMPTS`, report compact failure summary + log paths and **stop**
   - If return is `ERROR`, report and **stop** for manual intervention
   - Stage the changed files with `git -C {repo_root} add <specific files>` — NEVER use `git add -A` or `git add .`
   - If staged diff is empty after processing feedback, report "No code changes required from actionable feedback; re-running reviews." and go back to Loop Start without creating a commit
   - Create a **new commit** (not amend) describing what was fixed:
     - If `{jira_ticket}` is present: JIRA ticket prefix followed by conventional commit format
     - If `{jira_ticket}` is empty: use conventional commit format only
   - Enforce forbidden commit trailer policy (same checks as Phase 2 step 12); stop on violation
   - Persist remediation commit metadata to `{artifacts_root}/commits/review-iteration-{iteration}.json`
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/commits/review-iteration-{iteration}.json"])`; on `ERROR`, stop for manual intervention
   - Go back to Loop Start (re-run ALL four reviewers, not just the ones that failed — they see the full cumulative diff via `git -C {repo_root} diff {base_hash}..HEAD` and all commit messages)

## Phase 4: Optional Squash and Finalize

1. If there are multiple commits since `base_hash` (i.e. review fixes were applied):
   - Apply resolved `squash_mode` from Phase 1:
     - `off`: do not rewrite history; keep commits as-is and report their hashes/titles
     - `auto`: squash now (all reviewers approved and there are multiple commits)
     - `on`: squash now
   - If `squash_mode` is `auto` or `on`, run `git reset --soft {base_hash}` then `git commit`
   - For squash commits, write a clean, atomic commit as if the implementation was done right the first time:
     - **Subject line**:
       - If `{jira_ticket}` is present: JIRA ticket prefix followed by conventional commit format (e.g. `CAT-000 feat(scope): description`)
       - If `{jira_ticket}` is empty: use conventional commit format only (do not invent a ticket)
     - **Body**: detailed implementation description with full context (what changed, why, and how)
     - No references to review iterations, fixes, or the review process
     - No Co-Authored-By tags or "Generated with Claude Code" signatures
2. Run `git -C {repo_root} log -1` to confirm the final commit
   - Also run `git -C {repo_root} log -1 --pretty=%B` and enforce forbidden trailer policy (same checks as Phase 2 step 12)
   - Persist final commit metadata to `{artifacts_root}/commits/final-commit.json`
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/commits/final-commit.json"])`; on `ERROR`, stop for manual intervention
3. Report the commit hash and message
   - Include `artifacts_root` in the final report so verification/review evidence remains available even when transcript is compacted

## Error Handling

| Case | Action |
|------|--------|
| No plan file found | Report "No plan file found. Use plan mode first to create a plan." and stop |
| Plan file path doesn't exist | Report the error with the path and stop |
| Conflicting squash flags (`--squash` + `--no-squash`) | Report conflict and stop |
| No plan selected | Report "No plan selected." and stop |
| Not in git repo | Report the error and stop |
| Working tree is not clean | Report dirty files and stop |
| `artifacts_root` fails validation (empty/non-absolute/outside `/tmp/implement-runs/`) | Report and stop |
| No changes after coding phase | Report "No changes produced" and stop |
| Verification loop reaches max attempts | Report compact failure summary + log paths and stop |
| No verification command could be executed | Report that verification could not be executed and stop |
| Verification loop returns ERROR | Stop and report manual intervention needed |
| Plan-specified verification command is substituted, skipped, or removed beyond exact-duplicate normalization and approved effectiveness-enforcement flags | Stop and report manual intervention needed |
| Verification result command manifest mismatch (missing/extra/mutated command entries) | Stop and report manual intervention needed |
| Required verification fails only on files outside `verification_scope_files` snapshot | Stop and report out-of-scope/pre-existing failure for manual intervention |
| Verification-fix attempt introduces edits outside `verification_scope_files` | Stop and report out-of-scope edits; do not continue auto-fixing |
| Required persisted artifact file is missing/empty after a write step | Stop and report missing artifact paths for manual intervention |
| Verification coordinator output is missing/malformed terminal JSON summary | Re-run coordinator once; if still malformed, stop for manual intervention |
| Verification worker drain counters are inconsistent (`workers_completed != workers_spawned` or `workers_inflight > 0`) | Treat as coordinator error; re-run once, then stop for manual intervention |
| Required test gate is ineffective (e.g., disabled/no tests executed) | Stop and report manual intervention needed unless user explicitly approves fallback |
| Verification reports PASS but workers are still running | Treat as coordinator error; re-run once, then stop for manual intervention |
| Background worker completion appears after an accepted PASS verdict | Treat prior verdict as invalid; re-run verification once, then stop for manual intervention if repeated |
| Required test gate returns PASS but with `tests_executed <= 0` or `gate_effective != true` | Treat as verification error and stop for manual intervention |
| Verification status includes non-protocol values (for example `PASS_WITH_PREEXISTING_FAILURE`, `FAIL_PREEXISTING`) | Treat as protocol violation and stop for manual intervention |
| Required verification command contains output-truncating wrapper or `/dev/null` sink | Stop and report offending command id/text for manual intervention |
| Any verification command is executed directly in orchestrator (outside coordinator) | Stop and report policy violation for manual intervention |
| Attempting to run ad-hoc baseline verification probes in orchestrator (for example `git stash` + build/test reruns) | Stop and report policy violation for manual intervention |
| Plan non-command verification assertion cannot be deterministically checked | Stop and report manual intervention needed |
| Plan non-command verification assertion check fails | Report assertion failure and stop |
| Plan conformance checklist has unmapped explicit requirement(s) | Stop and report unmet requirement ids/anchors |
| Plan conformance returns `FAIL`/`ERROR` and flow attempts to continue | Stop and report policy violation for manual intervention |
| Empty staged diff | Report "Nothing staged" and stop |
| Commit body missing/trivial | Report and stop for manual intervention |
| Commit contains forbidden trailer (`Co-Authored-By` / `Generated with Claude Code`) | Stop and report exact trailer lines for manual intervention |
| APPROVE verdict missing required evidence section/anchors | Re-run that reviewer once; if still invalid, stop for manual intervention |
| Reviewer output uses bare filenames instead of repo-relative paths in evidence/issues | Re-run that reviewer once; if still invalid, stop for manual intervention |
| Contract-shift detected but architecture-reviewer APPROVE lacks `Caller Impact` evidence | Re-run architecture-reviewer once; if still invalid, stop for manual intervention |
| Contract-shift detected but test-reviewer APPROVE lacks explicit test coverage citations | Re-run test-reviewer once; if still invalid, stop for manual intervention |
| Optional polish pass verification fails | Report compact failure summary + log paths and stop |
| Max iterations reached | Leave commits as-is (not squashed), report all remaining unresolved issues |
| Reviewer fails twice | Stop and report reviewer name for manual intervention |
| Git commit fails | Report the error output, do not retry |
| Pre-commit hook fails | Report the hook output, do not retry |

## Important Rules

- NEVER use `git add -A` or `git add .` — always stage specific files
- Always execute git commands as `git -C {repo_root} ...` after Phase 0 captures `repo_root`
- NEVER add Co-Authored-By lines or "Generated with Claude Code" to commits
- Keep `Review loop` and `RunVerificationLoop` separate; do not merge their responsibilities
- Build `verification_scope_files` from tracked + untracked files (not tracked-only diffs)
- Prefer maximum safe parallelism: make read-only independent checks `parallel_safe=true` in the same stage
- Always re-run ALL four reviewers each iteration, even if only one requested changes
- Use adaptive review depth (`focused` vs `deep`) based on diff size/risk while still running all four reviewers
- Always run verification via `verification-coordinator` (which delegates to `verification-worker`) rather than running raw verification commands directly in the orchestrator
- Never invoke `verification-coordinator` directly from Phase 2/3 flow; always go through `RunVerificationLoop`
- Never run required verification gates with output-truncating wrappers (`| tail`, `| head`, `| sed -n`, pagers) or `/dev/null` sinks
- Never proceed on non-terminal/backgrounded verification output; always wait for terminal coordinator JSON, `workers_inflight=0`, and `workers_completed==workers_spawned`
- Never substitute, bypass, or drop explicit plan-specified verification commands without explicit user approval, except exact duplicate removal after normalization and approved effectiveness-enforcement flags for required test gates
- Treat the phase2 verification manifest as immutable for the rest of the run (same command ids/order/text/metadata)
- Never auto-fix verification failures by editing out-of-scope files; stop for manual intervention when failures are pre-existing/out-of-scope
- Never reclassify required-gate failures as pass/non-blocking/pre-existing in order to continue
- Never accept verification results whose command list does not exactly match the emitted `verification_manifest`
- Never accept verification results when `command_manifest_validated` is false
- For required test gates, treat "no effective execution" (disabled/skipped-only/zero tests) as failure, not pass
- Persist machine-readable verification summaries/manifests and reviewer verdict artifacts under `/tmp/implement-runs/{implement_run_id}`
- Persist per-attempt coordinator artifacts and per-iteration reviewer artifacts for every iteration (`iteration-1`, `iteration-2`, ...)
- Audit required artifact paths after each persistence step; missing artifacts are hard failures
- Never accept evidence-free APPROVE verdicts from reviewers
- Never accept reviewer evidence that only uses bare filenames; require repo-relative paths
- For contract-shift changes, require architecture-reviewer approvals to include `Caller Impact` evidence (`Changed callable` + caller compatibility evidence, or explicit `No in-repo callers found` when true)
- For contract-shift changes, require test-reviewer approvals to cite existing covering tests (`path:line` + test name) or block with REQUEST_CHANGES
- Before committing, ensure explicit plan requirements are mapped to concrete implementation/test anchors via the conformance checklist
- If conformance returns `FAIL`/`ERROR`, stop; do not override by narrative judgement
- Do not auto-insert mutating verification commands unless plan-required or strictly necessary to create a meaningful gate
- Never inline full verification stdout/stderr in the orchestrator context; keep only compact summaries and log paths
- Never "fix" verification by weakening tests (removed assertions, broad allowlists, skipped failing cases) unless the behavior change is explicitly intended and documented
- After broad edits, always inspect touched files and undo unintended hunks before verification
- Never accept required test-gate PASS results that only show cache/up-to-date/no-source execution with zero executed tests
- When all reviewers approve but provide non-blocking recommendations, run a polish pass only for convergent (2+ reviewers) actionable items
- Never use `git stash` / `git stash pop` in this workflow to probe verification state
- Do not rewrite history with `git reset --soft` unless `squash_mode` resolves to `auto` or `on`
- The commit message should reflect what was implemented, not the review process
- Every implementation commit must include a substantive body (what/why/how), not subject-only
- Never create "fix feedback" commits when no files actually changed
- Never invent a JIRA ticket if branch name does not contain one
- When fixing blocking bug/correctness feedback, include a regression test for the missed edge case (or document why not feasible)
