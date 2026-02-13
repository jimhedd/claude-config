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

## Context Budget And State Policy

To protect long runs from context-window compaction and stale async events:

- Keep orchestrator narrative compact and artifact-first: summarize with ids/status/paths, never paste large logs or full reviewer prose into the main thread.
- Treat persisted artifacts under `{artifacts_root}` as authoritative state, not transient background task messages.
- Persist compact state checkpoints after each phase and review iteration; resume decisions from checkpoints when context compaction occurs.
- Never re-hydrate full `{plan_contents}` into every reviewer prompt after Phase 1; use a compact digest + iteration prompt pack.
- Run in transcript quiet mode by default:
  - For `Write`/`Edit`/`Update`, report only artifact path + brief status (`created/updated`, bytes/line delta).
  - For `Read`, do not inline full file bodies unless required for a failing gate/parsing decision.
  - For `Bash`, print compact outcome summaries; persist full outputs to artifact log files and reference paths.
  - If any inline excerpt is necessary, cap to <= 20 lines and include why the excerpt is required.

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
     - `{artifacts_root}/state`
   - Write `{artifacts_root}/run_context.json` with at least:
     - `implement_run_id`, `repo_root`, `branch`, `jira_ticket`, `started_at_utc`
   - Write `{artifacts_root}/state/run-state.json` with at least:
     - `run_state="PHASE0_INITIALIZED"`
     - `state_epoch=0`
     - `review_iteration=0`
     - `finalized=false`
     - `authoritative_review_iteration=null`
   - Invoke `PersistRunStateCheckpoint(checkpoint_name="phase0-initialized", phase="phase0", summary={"repo_root": repo_root, "branch": branch})`

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

6. Display only a compact plan preview (path + key requirement ids + resolved `squash_mode`), then proceed directly to coding
   - Selecting the plan (path argument or AskUserQuestion choice) is the confirmation to execute
   - Persist `plan_path`, `squash_mode`, and `{plan_contents}` to:
     - `{artifacts_root}/run_context.json` (update existing JSON)
     - `{artifacts_root}/plan.md`
   - Build and persist a compact `plan_review_digest` for reviewer prompts:
     - include only objective, explicit invariants/constraints, explicit out-of-scope clauses, and required verification gates
     - omit implementation detail prose and long examples
     - target <= 120 lines and <= 8KB
     - persist to `{artifacts_root}/plan-review-digest.md`
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/plan-review-digest.md"])`; on `ERROR`, stop for manual intervention
   - Invoke `PersistRunStateCheckpoint(checkpoint_name="phase1-plan-loaded", phase="phase1", summary={"plan_path": plan_path, "squash_mode": squash_mode, "plan_review_digest_path": "{artifacts_root}/plan-review-digest.md"})`

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
   - Context-protection default for optional pre-test compile gates:
     - Do **not** auto-add compile/typecheck pre-gates by default (to avoid extra verification command executions and token cost).
     - Add a pre-gate only when explicitly required by `{plan_contents}` or when the immediately previous verification attempt failed due to compile/setup prerequisites.
     - If added, mark it `required=false`, run it once before required test gates, and do not duplicate a signal already covered by a required gate.
   - Auto-added mutating commands policy:
     - prefer non-mutating verification commands first
     - add mutating commands (formatters/auto-fixers/code generators) only when explicitly required by `{plan_contents}` or when they are the only viable way to produce a meaningful verification gate
   - Minimize redundant auto-detected gates:
     - If an aggregate command already covers lower-level checks (for example, `check` covering test/lint), avoid adding duplicate commands unless explicitly required by the plan
   - Normalize commands to run from verification-loop `cwd`:
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
     - If an explicit pre-test compile gate exists, place it in the earliest read-only stage and shift required test gates to the next read-only stage
     - If plan includes both targeted and aggregate read-only gates, schedule targeted gates in the earliest read-only stage and aggregate gates in the latest read-only stage
   - If all commands are mutating or non-parallel-safe, expect serial execution and do not force parallelism
   - Emit a deterministic `verification_manifest` before execution (for auditability):
     - Print only compact command metadata: `id`, `stage`, `gate_type`, `required`, `must_be_effective` (do not print full command text inline)
     - Preserve this manifest and treat it as the source-of-truth command set for all subsequent verification-loop parsing
     - If `deduped_redundant_commands` is non-empty, print only count + artifact path (no inline rationale prose)
     - Persist the machine-readable manifest to `{artifacts_root}/verification/phase2-initial-manifest.json`
     - Store this as `verification_manifest` for all later `RunVerificationLoop` invocations
     - Treat this manifest as immutable for the entire run:
       - do not add/remove/reorder commands in later review-loop verification passes
       - do not mutate command text/metadata outside allowed `source_command` effectiveness normalization already captured in this manifest
     - if later verification execution cannot run this manifest as-is, stop for manual intervention
7. Build and execute auditable setup commands before verification:
   - Build `setup_commands` list for environment prerequisites required by this run (for example auth/bootstrap commands such as docker login).
   - Sources for setup detection:
     - explicit plan prerequisites in `{plan_contents}`
     - repo-local AGENTS/README execution prerequisites in scope
     - deterministic toolchain prerequisites required by selected verification gates
   - Persist setup manifest to `{artifacts_root}/verification/phase2-setup-manifest.json` with:
     - `commands`: ordered list of `{id, command, required, timeout_seconds}`
     - `source_notes` for each command
   - Execute setup commands serially in manifest order before `RunVerificationLoop`:
     - execute from `repo_root`
     - capture full stdout/stderr to `{artifacts_root}/verification/phase2-setup-<id>.log`
     - record compact per-command status in setup summary
   - Persist setup summary to `{artifacts_root}/verification/phase2-setup-summary.json` with:
     - `overall_status`
     - `commands_total`
     - per-command `{id, status, exit_code, duration_ms, log_path}`
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/verification/phase2-setup-manifest.json", "{artifacts_root}/verification/phase2-setup-summary.json"])`; on `ERROR`, stop for manual intervention
   - If any `required=true` setup command fails or errors, stop and report manual intervention needed (do not continue to verification)
8. If no verification command could be executed, report that and **stop** (do not commit)
9. Invoke `RunVerificationLoop(verification_commands, verification_manifest, context_label="phase2-initial")`
   - `RunVerificationLoop` is the only allowed verification execution entry point in all phases and uses one uniform direct-execution path for every run
   - If return is `FAIL_MAX_ATTEMPTS`, report compact failure summary + log paths and **stop** (do not commit)
   - If return is `ERROR`, report and **stop** for manual intervention
   - If return is `PASS`, print only a one-line verification status summary plus artifact path(s) (no inline per-command table in transcript)
   - If return has any other status/value, treat it as protocol violation and **stop** for manual intervention
   - Persist latest verification summary JSON to:
     - `{artifacts_root}/verification/phase2-initial-summary.json`
   - Invoke `AuditRunArtifacts(required_paths)` for:
     - `{artifacts_root}/verification/phase2-initial-summary.json`
     - `{artifacts_root}/verification/phase2-initial-manifest.json`
     - If return is `ERROR`, report missing artifact paths and **stop**
   - Then invoke `ValidateVerificationAssertions(verification_assertions, verification_results, repo_root)`:
     - If return is `PASS`, continue
     - If return is `FAIL` or `ERROR`, report compact assertion failure summary and **stop**
   - Then invoke `ValidatePlanConformanceChecklist(plan_contents, repo_root, diff_range="{base_hash}")`:
     - Persist outputs to:
       - `{artifacts_root}/verification/phase2-plan-conformance.json`
     - Invoke `AuditRunArtifacts(required_paths)` for persisted conformance files; on `ERROR`, stop for manual intervention
     - If return is `FAIL` or `ERROR`, report compact unmet-requirements summary and **stop**
   - Never reinterpret verification or conformance failures as effective pass (for example “pre-existing/out-of-scope but proceed anyway”); fail closed and stop
   - Invoke `PersistRunStateCheckpoint(checkpoint_name="phase2-verified", phase="phase2", summary={"setup_summary": "{artifacts_root}/verification/phase2-setup-summary.json", "verification_summary": "{artifacts_root}/verification/phase2-initial-summary.json", "conformance_summary": "{artifacts_root}/verification/phase2-plan-conformance.json"})`
10. Stage the relevant files with `git -C {repo_root} add <specific files>` — NEVER use `git add -A` or `git add .`
11. If staged diff is empty, report "Nothing staged" and **stop**
12. Create a commit with a detailed message:
   - **Subject line**:
     - If `{jira_ticket}` is present: JIRA ticket prefix followed by conventional commit format (e.g. `CAT-000 feat(scope): description`)
     - If `{jira_ticket}` is empty: use conventional commit format only (do not invent a ticket)
   - **Body**: spell out all goals and implementation details — what is being changed, why, and how. Include enough context that a reviewer reading only the commit message understands the full intent and scope of the change. Reference the plan file contents for context.
   - Build commit message explicitly as subject + body (for example via separate `-m` flags) so the body cannot be omitted accidentally
   - Body must contain concrete "what/why/how" details; minimum 3 non-empty lines
   - No Co-Authored-By tags or "Generated with Claude Code" signatures
13. Validate commit message completeness:
   - Run `git -C {repo_root} log -1 --pretty=%B`
   - If the commit body is missing or trivial, report and **stop** for manual intervention before entering review loop
   - Enforce forbidden commit trailers:
     - Reject commit messages containing any line that starts with:
       - `Co-Authored-By:`
       - `Generated with Claude Code`
     - If found, report the exact trailer lines and **stop** for manual intervention
   - Persist the created commit metadata to `{artifacts_root}/commits/phase2-initial-commit.json` (`hash`, `subject`, `body`)
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/commits/phase2-initial-commit.json"])`; on `ERROR`, stop for manual intervention
   - Invoke `PersistRunStateCheckpoint(checkpoint_name="phase2-committed", phase="phase2", summary={"commit_metadata": "{artifacts_root}/commits/phase2-initial-commit.json", "base_hash": base_hash})`

## Shared Procedure: RunVerificationLoop

Use this exact procedure in both Phase 2 and Phase 3.

Inputs:

- `verification_commands`
- `context_label` (for example: `phase2-initial`, `review-iteration-2`)
- `repo_root`
- `verification_manifest` (the immutable manifest persisted at `{artifacts_root}/verification/phase2-initial-manifest.json`)

Outputs:

- `status`: `PASS` | `FAIL_MAX_ATTEMPTS` | `ERROR`
- `verification_results`: parsed verification summary JSON object from the latest attempt (required when `status=PASS`)

Algorithm:

1. Set `verification_fix_attempt = 0` (single-attempt fail-fast mode)
2. Enforce immutable command set before execution:
   - `verification_commands` must exactly match `verification_manifest.commands` (length, order, ids, command text, and execution metadata).
   - On mismatch, return `ERROR` (command drift/manual intervention).
3. Capture immutable in-scope files and persist `{artifacts_root}/verification/{context_label}-scope-files.txt`:
   - union of tracked (`git diff --name-only`) and untracked (`git ls-files --others --exclude-standard`) repo-relative paths.
   - if empty, return `ERROR`.
4. Execute one terminal verification attempt:
   - Transcript budget per attempt: at most 4 orchestrator lines (`start`, `terminal status`, optional compact failure digest, artifact path). Do not emit invariant-by-invariant commentary.
   - Execute verification commands directly from `verification_manifest.commands` (single path for every run; no verification subagents).
   - For each command in manifest order:
     - run from `repo_root` with Bash pipefail and file-only logs:
       - `/bin/bash -o pipefail -c "<command>" >"{artifacts_root}/verification/{context_label}-attempt-{verification_fix_attempt}-{command_id}.log" 2>&1`
     - capture `exit_code`, `duration_ms`, and `log_path`
     - map status deterministically: `exit_code==0 -> PASS`, non-zero -> `FAIL`; execution/parsing failure -> `ERROR`
     - compute effectiveness metadata:
       - for required `gate_type=test`, require explicit evidence of tests executed (`tests_executed > 0`) from current attempt log
       - when no-op/no-tests markers appear (for example `UP-TO-DATE`, `FROM-CACHE`, `NO-SOURCE`, `0 tests`, `No tests found`), set `gate_effective=false` with `ineffective_reason`
     - on first required command with `status!=PASS` or ineffective required test gate, stop scheduling further commands in this attempt and mark remaining required commands as `ERROR` with `summary="skipped_after_required_failure"`
   - Persist parsed summary to `{artifacts_root}/verification/{context_label}-attempt-{verification_fix_attempt}.json`.
   - Validate protocol invariants:
     - canonical statuses only (`PASS|FAIL|ERROR`)
     - `results.length==len(verification_commands)`
     - manifest fidelity: one result per `command_id`; no extras
     - required test gates must have `status=PASS`, `gate_effective=true`, `tests_executed>0`
   - Decision:
     - all required commands pass with effective required test gates => return `PASS` with `verification_results`
     - any required command `ERROR` => return `ERROR`
     - any required failure => `FAIL_MAX_ATTEMPTS`; surface only compact `short_failure_digest` + log paths (<= 6 lines total) and stop
5. Never proceed while required verification commands are failing.

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
6. Print only one line with aggregate assertion counts (`total/pass/fail/error`); persist detailed evidence to artifact files when needed.

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
- `diff_range`:
  - use `{base_hash}` for pre-commit validation (compares working tree/index against base)
  - use `{base_hash}..HEAD` for committed-range validation when needed

Algorithm:

1. Extract explicit implementation requirements from the plan (numbered steps, normative bullets, and stated invariants).
2. Assign deterministic requirement ids (`REQ-001`, `REQ-002`, ...).
3. For each requirement, map to at least one implementation anchor in the diff range (`path:line`).
4. If a requirement references a named file/symbol that is not present in the current tree but can be deterministically resolved from git history, allow a historical anchor (`history:<commit>:<path>:line`) plus current diff/test anchors.
5. For behavior/correctness requirements (validation, dedup/grouping, ordering/mapping, error classification, request/response contracts), also require at least one test anchor (`path:line` + test name) in the diff range, unless infeasible reason is explicitly documented.
6. Produce a checklist with columns: `requirement_id`, `requirement`, `impl_anchor`, `test_anchor`, `status`, `notes`.
7. Persist checklist to `{artifacts_root}/verification/phase2-plan-conformance.json` only.
8. If any explicit requirement is unmapped or weakly evidenced, return `FAIL`.
9. If extraction/mapping cannot be done deterministically, return `ERROR`.
10. Otherwise return `PASS`.
11. Never override `FAIL`/`ERROR` by narrative judgement; checklist result is authoritative for gating.

## Shared Procedure: PersistRunStateCheckpoint

Inputs:

- `checkpoint_name`
- `phase`
- `summary` (compact JSON-serializable object)

Algorithm:

1. Read `{artifacts_root}/state/run-state.json` if present; if missing, treat as `ERROR`.
2. Increment `state_epoch` by 1.
3. Write `{artifacts_root}/state/{checkpoint_name}.json` with:
   - `state_epoch`
   - `phase`
   - `review_iteration` (if in Phase 3)
   - `head_hash` (`git -C {repo_root} rev-parse HEAD` when available)
   - compact `summary` payload
4. Update `{artifacts_root}/state/run-state.json` to mirror latest:
   - `run_state` (set to `summary.run_state_override` when provided; otherwise `{phase}:{checkpoint_name}`)
   - `state_epoch`
   - `review_iteration`
   - `authoritative_review_iteration` when known
   - `last_checkpoint_path`
5. Keep checkpoint files compact (target <= 4KB) and artifact-path-driven.
6. Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/state/run-state.json", "{artifacts_root}/state/{checkpoint_name}.json"])`; on `ERROR`, stop for manual intervention.

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
3.6 Build a compact `review_prompt_pack` and persist to `{artifacts_root}/reviews/iteration-{iteration}/prompt-pack.md`:
   - create iteration directory first: `{artifacts_root}/reviews/iteration-{iteration}`
   - include `git -C {repo_root} diff --stat {base_hash}..HEAD`
   - include changed file list (repo-relative, deterministic order)
   - include unresolved blocking issues from prior iteration verdict JSON only (ids/titles/paths; no raw prose copy)
   - include artifact pointers for latest verification/conformance summaries
   - target <= 160 lines and <= 10KB
   - invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/reviews/iteration-{iteration}/prompt-pack.md"])`; on `ERROR`, stop for manual intervention

4. Spawn ALL 4 reviewer agents **in parallel** using the Task tool. Each Task call must:
   - Use the appropriate `subagent_type` for the reviewer
   - Include this prompt structure:

   ```
   Review the git changes using the compact plan digest and iteration context below.

   Plan digest:
   {plan_review_digest}

   Iteration context:
   {review_prompt_pack}

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
   Keep output compact; avoid long narrative digressions and limit findings to the highest-value actionable items.
   If concern is uncertain or preference-only, classify it as a nitpick.
   Return your verdict in the structured format specified in your agent instructions.
   ```

5. Wait for all 4 reviewers to complete
   - Create the iteration directory first (`{artifacts_root}/reviews/iteration-{iteration}`)
   - Persist reviewer task handles map to `{artifacts_root}/reviews/iteration-{iteration}/task-handles.json` before waiting
   - Persist each reviewer raw output verbatim from Task output to `{artifacts_root}/reviews/iteration-{iteration}/<reviewer>-attempt-1.md` (do not synthesize or rewrite content)
   - Invoke `AuditRunArtifacts(required_paths)` for:
     - `{artifacts_root}/reviews/iteration-{iteration}/task-handles.json`
     - `{artifacts_root}/reviews/iteration-{iteration}/code-quality-reviewer-attempt-1.md`
     - `{artifacts_root}/reviews/iteration-{iteration}/architecture-reviewer-attempt-1.md`
     - `{artifacts_root}/reviews/iteration-{iteration}/test-reviewer-attempt-1.md`
     - `{artifacts_root}/reviews/iteration-{iteration}/bug-reviewer-attempt-1.md`
     - If return is `ERROR`, stop for manual intervention
   - Treat these persisted attempt files as the only parse source; do not parse transient background status text

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
   - Treat REQUEST_CHANGES output as unparseable unless every `#### Issue N:` block contains:
     - `**File**` with a repo-relative path
     - `**Line(s)**`
     - `**Diff Line(s)**` anchored to changed lines in `{base_hash}..HEAD`
     - `**Severity**` (`high | medium | low`)
     - `**Category**`
     - `**Problem**`
     - `**Suggestion**`
   - Treat reviewer output as unparseable when it uses synthetic placeholders instead of concrete evidence/issues (for example phrases like `Full evidence provided`, `details omitted`, or equivalent summary-only stubs)
   - Treat output as unparseable if issue/nitpick/recommendation file references are not repo-relative paths
   - Detect whether contract shifts are present in `{base_hash}..HEAD` (for example: changed function signatures, moved validation/filtering responsibility, changed preconditions, or caller/callee contract shifts)
   - If contract shifts are present, treat `architecture-reviewer` APPROVE as unparseable unless it includes `#### Caller Impact` with:
     - at least one `Changed callable:` evidence anchor (`path:line`), and
     - either at least one `Caller evidence N:` caller-site anchor (`path:line`) with compatibility rationale, or explicit `No in-repo callers found` justification
   - If contract shifts are present, treat `test-reviewer` APPROVE as unparseable unless at least one evidence item cites an existing covering test with `path:line` and test name
   - If it still fails/unparseable after retry, report "Reviewer <name> failed twice; stopping for manual intervention." and **stop**
   - Persist authoritative iteration decision to `{artifacts_root}/reviews/iteration-{iteration}/decision.json` with:
     - `state_epoch`
     - `iteration`
     - canonical verdict per reviewer
     - exact source attempt/verdict artifact paths used for parsing
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/reviews/iteration-{iteration}/decision.json"])`; on `ERROR`, stop for manual intervention
   - Invoke `PersistRunStateCheckpoint(checkpoint_name="review-iteration-{iteration}-verdict", phase="phase3", summary={"decision_path": "{artifacts_root}/reviews/iteration-{iteration}/decision.json"})`

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
     - Invoke `ValidatePlanConformanceChecklist(plan_contents, repo_root, diff_range="{base_hash}")`:
     - Persist outputs to:
       - `{artifacts_root}/verification/review-polish-{iteration}-plan-conformance.json`
     - Invoke `AuditRunArtifacts(required_paths)` for persisted conformance files; on `ERROR`, stop for manual intervention
     - If return is `FAIL` or `ERROR`, stop and report unmet requirement ids/anchors (do not auto-continue polish)
     - If return is `FAIL_MAX_ATTEMPTS`, report compact failure summary + log paths and **stop**
     - If return is `ERROR`, report and **stop** for manual intervention
     - Stage changed files with `git -C {repo_root} add <specific files>` (never `git add -A` / `git add .`)
     - If staged diff is empty, report "No safe polish changes applied; proceeding to finalize." and break to Phase 4
     - Create a new commit (not amend) describing the polish changes with a substantive body
     - Enforce forbidden commit trailer policy (same checks as Phase 2 step 13); stop on violation
     - Persist polish commit metadata to `{artifacts_root}/commits/review-polish-{iteration}.json`
     - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/commits/review-polish-{iteration}.json"])`; on `ERROR`, stop for manual intervention
     - Go back to Loop Start (re-run all 4 reviewers on cumulative diff)

9. If ANY return REQUEST_CHANGES:
   - Compile all feedback from authoritative iteration decision + reviewer verdict artifacts only (not transient background completion text) into a unified issue list
   - De-duplicate overlapping issues and prioritize high → medium → low
   - Ignore nitpicks for gating purposes
   - Address each issue: read context, make the fix, verify the fix
   - For each blocking bug/correctness issue, add or update an automated regression test that covers the previously missed edge case
   - For any remediation that changes functional behavior (validation/parsing/grouping/mapping/error handling), add or update an automated regression test in the same iteration
   - Prefer writing the regression test before the code fix; at minimum, ensure the new test would fail before the fix and pass after the fix
   - If a regression test is not feasible, document the reason in the remediation commit message
   - If a proposed remediation would violate explicit plan invariants (including `Not changed` or explicitly out-of-scope sections), do not auto-apply that remediation:
     - report the conflicting reviewer issue and plan clause
     - request explicit user approval to expand scope; if not approved, keep scope unchanged and continue triage
   - Invoke `RunVerificationLoop(verification_commands, verification_manifest, context_label="review-iteration-{iteration}")`
   - If return is `PASS`, invoke `ValidateVerificationAssertions(verification_assertions, verification_results, repo_root)`:
     - If assertion validation returns `FAIL` or `ERROR`, report and **stop**
   - Invoke `ValidatePlanConformanceChecklist(plan_contents, repo_root, diff_range="{base_hash}")`:
     - Persist outputs to:
       - `{artifacts_root}/verification/review-iteration-{iteration}-plan-conformance.json`
     - Invoke `AuditRunArtifacts(required_paths)` for persisted conformance files; on `ERROR`, stop for manual intervention
     - If return is `FAIL` or `ERROR`, stop and report unmet requirement ids/anchors before committing remediation changes
   - If return is `FAIL_MAX_ATTEMPTS`, report compact failure summary + log paths and **stop**
   - If return is `ERROR`, report and **stop** for manual intervention
   - Stage the changed files with `git -C {repo_root} add <specific files>` — NEVER use `git add -A` or `git add .`
   - If staged diff is empty after processing feedback, report "No code changes required from actionable feedback; re-running reviews." and go back to Loop Start without creating a commit
   - Create a **new commit** (not amend) describing what was fixed:
     - If `{jira_ticket}` is present: JIRA ticket prefix followed by conventional commit format
     - If `{jira_ticket}` is empty: use conventional commit format only
   - Enforce forbidden commit trailer policy (same checks as Phase 2 step 13); stop on violation
   - Persist remediation commit metadata to `{artifacts_root}/commits/review-iteration-{iteration}.json`
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/commits/review-iteration-{iteration}.json"])`; on `ERROR`, stop for manual intervention
   - Invoke `PersistRunStateCheckpoint(checkpoint_name="review-iteration-{iteration}-committed", phase="phase3", summary={"remediation_commit": "{artifacts_root}/commits/review-iteration-{iteration}.json"})`
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
   - Also run `git -C {repo_root} log -1 --pretty=%B` and enforce forbidden trailer policy (same checks as Phase 2 step 13)
   - Persist final commit metadata to `{artifacts_root}/commits/final-commit.json`
   - Persist `{artifacts_root}/final-summary.json` with final status, final commit hash, verification summary path, conformance path, and authoritative review decision path
   - Persist atomic finalization checkpoint first to `{artifacts_root}/state/phase4-finalized.json` with:
     - `phase="phase4"`
     - `head_hash`
     - `summary.final_summary_path`
     - `summary.final_commit_hash`
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/commits/final-commit.json", "{artifacts_root}/final-summary.json", "{artifacts_root}/state/phase4-finalized.json"])`; on `ERROR`, stop for manual intervention
   - As the final atomic state transition, update `{artifacts_root}/state/run-state.json` last:
     - increment `state_epoch` by 1 from current value
     - `run_state="FINALIZED"`
     - `finalized=true`
     - `finalized_at_utc=<now>`
     - `authoritative_final_summary="{artifacts_root}/final-summary.json"`
     - `last_checkpoint_path="{artifacts_root}/state/phase4-finalized.json"`
   - Invoke `AuditRunArtifacts(required_paths=["{artifacts_root}/state/run-state.json"])`; on `ERROR`, stop for manual intervention
3. Report the commit hash and message
   - Include `artifacts_root` in the final report so verification/review evidence remains available even when transcript is compacted
   - After FINALIZED state is written, ignore late asynchronous reviewer/verification completion chatter for gating; append such events to `{artifacts_root}/state/late-events.log` for audit only

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
| Required test gate is ineffective (e.g., disabled/no tests executed) | Stop and report manual intervention needed unless user explicitly approves fallback |
| Verification command execution cannot be started or shell execution fails unexpectedly | Stop and report manual intervention needed |
| Verification attempt summary JSON is missing/unparseable | Stop and report manual intervention needed |
| Required test gate returns PASS but with `tests_executed <= 0` or `gate_effective != true` | Treat as verification error and stop for manual intervention |
| Verification status includes non-protocol values (for example `PASS_WITH_PREEXISTING_FAILURE`, `FAIL_PREEXISTING`) | Treat as protocol violation and stop for manual intervention |
| Required verification command contains output-truncating wrapper or `/dev/null` sink | Stop and report offending command id/text for manual intervention |
| Setup manifest/summary artifact missing or empty | Stop and report missing setup artifact paths for manual intervention |
| Required setup command fails before verification | Stop and report setup command id/log path for manual intervention |
| Attempting to run ad-hoc baseline verification probes in orchestrator (for example `git stash` + build/test reruns) | Stop and report policy violation for manual intervention |
| Plan non-command verification assertion cannot be deterministically checked | Stop and report manual intervention needed |
| Plan non-command verification assertion check fails | Report assertion failure and stop |
| Plan conformance checklist has unmapped explicit requirement(s) | Stop and report unmet requirement ids/anchors |
| Plan conformance returns `FAIL`/`ERROR` and flow attempts to continue | Stop and report policy violation for manual intervention |
| Review/polish remediation changes fail plan conformance against `{base_hash}` | Stop and report unmet requirement ids/anchors before commit |
| `plan-review-digest.md` missing/empty | Stop and report missing artifact path for manual intervention |
| Reviewer raw attempt artifact missing/empty | Stop and report missing artifact path for manual intervention |
| Reviewer REQUEST_CHANGES issue missing `Diff Line(s)` anchor in `{base_hash}..HEAD` | Re-run reviewer once with compactness/range reminder; if repeated, stop for manual intervention |
| Authoritative iteration `decision.json` missing/empty | Stop and report missing artifact path for manual intervention |
| Reviewer output contains placeholder/synthetic stubs (for example `Full evidence provided`) | Re-run reviewer once; if repeated, stop for manual intervention |
| REQUEST_CHANGES output missing required issue fields (`File`, `Line(s)`, `Diff Line(s)`, `Severity`, `Category`, `Problem`, `Suggestion`) | Re-run reviewer once; if repeated, stop for manual intervention |
| Proposed remediation contradicts explicit plan invariants/out-of-scope clauses | Ask user for explicit scope-expansion approval; if not approved, keep scope unchanged |
| Empty staged diff | Report "Nothing staged" and stop |
| Commit body missing/trivial | Report and stop for manual intervention |
| Commit contains forbidden trailer (`Co-Authored-By` / `Generated with Claude Code`) | Stop and report exact trailer lines for manual intervention |
| APPROVE verdict missing required evidence section/anchors | Re-run that reviewer once; if still invalid, stop for manual intervention |
| Reviewer output uses bare filenames instead of repo-relative paths in evidence/issues | Re-run that reviewer once; if still invalid, stop for manual intervention |
| Contract-shift detected but architecture-reviewer APPROVE lacks `Caller Impact` evidence | Re-run architecture-reviewer once; if still invalid, stop for manual intervention |
| Contract-shift detected but test-reviewer APPROVE lacks explicit test coverage citations | Re-run test-reviewer once; if still invalid, stop for manual intervention |
| Phase4 finalization checkpoint exists but `run-state.json` is not `FINALIZED` | Stop and report partial-finalization state for manual intervention |
| Late async reviewer/verification completion arrives after FINALIZED | Record to `state/late-events.log`, do not alter final verdict |
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
- Prefer the smallest deterministic verification gate set and run it in manifest order
- Always re-run ALL four reviewers each iteration, even if only one requested changes
- Use adaptive review depth (`focused` vs `deep`) based on diff size/risk while still running all four reviewers
- Always run verification through the single `RunVerificationLoop` direct-execution path for every run (no alternate modes)
- Never send full `{plan_contents}` to reviewers after Phase 1; use `{artifacts_root}/plan-review-digest.md` + iteration `prompt-pack.md`
- Keep reviewer prompt packs compact and deterministic; prefer artifact paths over prose duplication
- Never run environment prerequisite commands ad hoc (for example docker login); declare them in `phase2-setup-manifest.json` and record outcomes in `phase2-setup-summary.json`
- Keep transcript output in quiet mode: path/status summaries by default, with large outputs persisted to artifact logs
- Never run required verification gates with output-truncating wrappers (`| tail`, `| head`, `| sed -n`, pagers) or `/dev/null` sinks
- Never treat timed-out or non-zero helper commands (formatters/generators/scripts used during remediation) as success; resolve or stop before proceeding
- Never run verification via nested subagents from `/implement`; execute verification commands directly with Bash pipefail and file-only logs
- Never substitute, bypass, or drop explicit plan-specified verification commands without explicit user approval, except exact duplicate removal after normalization and approved effectiveness-enforcement flags for required test gates
- Treat the phase2 verification manifest as immutable for the rest of the run (same command ids/order/text/metadata)
- Never auto-fix verification failures by editing out-of-scope files; stop for manual intervention when failures are pre-existing/out-of-scope
- Never reclassify required-gate failures as pass/non-blocking/pre-existing in order to continue
- Never accept verification results whose command list does not exactly match the emitted `verification_manifest`
- For required test gates, treat "no effective execution" (disabled/skipped-only/zero tests) as failure, not pass
- Persist machine-readable verification summaries/manifests and reviewer verdict artifacts under `/tmp/implement-runs/{implement_run_id}`
- Persist per-attempt verification summary artifacts and per-iteration reviewer artifacts for every iteration (`iteration-1`, `iteration-2`, ...)
- Persist run-state checkpoints under `{artifacts_root}/state/` and treat them as authoritative when context compaction occurs
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
- Finalization must be atomic: write and audit phase4 checkpoint artifacts first, then set `run-state.json` to `FINALIZED` as the last mutation
- After writing FINALIZED state, never change gating decisions based on late async task text; only reopen if authoritative artifacts are invalid
