# dev-bot

Polls Jira Cloud for tickets labeled for the bot, checks each is implementable, has a coding agent
(`agy` or `claude`) implement it in an isolated git worktree, verifies the result, and opens a **draft** GitHub PR.
A human reviews and merges; the bot never merges and only pushes `bot/*` branches.

One bot instance serves any number of GitHub repos: each `[[repos]]` block in `config.toml` has its own Jira project(s), checks, path rules, workflows and skills (see [Repos](#repos)).

## Setup
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy config.example.toml config.toml   # edit Jira URL/JQL, repo settings
copy .env.example .env                 # JIRA_EMAIL, JIRA_API_TOKEN (GitHub falls back to `gh auth token`)
.venv\Scripts\python -m pytest         # no network or engine credits needed
```

## Use
```powershell
dev-bot --dry-run ticket VS-123    # full run on one ticket, no Jira writes / push / PR
dev-bot --dry-run run               # poll once, same dry behavior
dev-bot run                         # for real
```
Kill switch: create `~/.dev-bot/.bot-paused` (or whatever `work_dir` is) and runs exit immediately.
Schedule `dev-bot run` every ~15 minutes with Windows Task Scheduler; an internal lock prevents overlaps.

Verified commits: drop an SSH private key at `{work_dir}/signing_key` and add its public half to
GitHub (Settings -> SSH and GPG keys -> New SSH key -> key type **Signing Key**) on the account whose
token pushes; the bot then signs every commit and GitHub shows it as **Verified**. No signing key
there means commits are made unsigned, same as before.

## Repos
A ticket goes to the repo whose `jira_projects` lists the ticket's Jira project, so **one Jira project maps to exactly one repo** (config load fails if two repos claim the same project). To add a repo, add a `[[repos]]` block; `jql = '{projects} AND ...'` expands to `project in ("VS", "DS", ...)` from all repos, so the query needs no edit. `config.example.toml` has a Spec Kit app and a spec-free component library side by side.

Per-repo settings beyond the basics (`setup_commands`, `check_commands`, `allowed_paths`, `denied_paths`, `max_diff_lines`):

| Setting | Effect |
|---|---|
| `workflows` | Which of `bugfix`/`feature`/`amend`/`task` the repo runs. A repo without Spec Kit sets `["bugfix", "task"]`: it is never sent down a spec path, its bugfix prompt drops the spec classes, and a `bot-feature`/`bot-amend` label bounces the ticket with an explanation. |
| `env` | Extra environment for setup and check commands; `{work_dir}` expands to the bot's `work_dir` (e.g. `npm_config_cache`). |
| `transitions` | Override the Jira transition names for this repo's projects (`[repos.transitions]`). |
| `skills_include`, `skills_exclude`, `skills_max`, `skills_dirs` | Which of the repo's own skills are offered to the engine (below). |
| `bugfix_skill` | Name of the repo's bugfix skill; the bugfix prompt points the engine at it. |
| `read_first` | Repo files (rules, constitution) the engine must read before coding. Missing files are skipped with a warning. |
| `instructions` | Free-text rules for this repo, added to every code-writing prompt. |

### Repo skills
Repos keep agent skills in `.agents/skills/<name>/SKILL.md` (mirrored to `.claude/skills`). Before the engine writes code the bot lists the relevant ones in the prompt (name, one-line description, path) and tells the engine to read the ones that apply and follow them. That works for any engine that can read files, whether or not it discovers skills itself. Skills are read from the worktree, which is cut from `origin/<default branch>`, so only merged content is used.

The engine runs headless and cannot run commands, and the bot owns git, installs and checks. The prompt tells it to skip any skill step that commits, pushes, opens a PR, installs packages or runs commands. Use `skills_exclude` for skills that are mostly that (`pr-creator`, `git-atomic-commit`, ...), or `skills_include` for a repo with dozens of skills; `bugfix_skill` and the Spec Kit stage skills are found even if filtered out of the list. A dry run (`dev-bot --dry-run -v ticket KEY`) logs which skills were offered.

## How a ticket flows
claim (label `bot-claimed` in Jira) -> readiness check (vague tickets get questions, no code) -> worktree off
`origin/<default>` -> engine edits -> guardrails (non-empty diff, size cap, allowed/denied paths) -> setup + check
commands from config -> re-verify diff -> commit (repo hooks run) -> push -> draft PR -> comment + transition in Jira.
Failures comment on the ticket, move it to the `needs_info` status, keep the claim label, and notify Slack if configured.

## Workflows (orchestrator)
After the readiness check the bot routes each ticket (`orchestrator.py`), cheapest signal first:
1. a `bot-bugfix` / `bot-feature` / `bot-amend` label (configurable under `[jira.workflow_labels]`);
2. Jira issue type `Bug` -> **bugfix**;
3. the ticket quotes an existing `specs/NNN-slug` folder, or its key is the `**Jira Key**` of exactly one spec -> **amend**;
4. otherwise the engine classifies it among the workflows the repo allows (an unusable or disallowed answer falls back to **task**; with only one allowed workflow there is no engine call).

| Workflow | What runs |
|---|---|
| `bugfix` | one engine call following the repo's bugfix skill if it has one (spec-aware repos: class A-D; others: A or D); needs a changed test file; class D, or a class B/C whose spec change is wide (the agent ends with `ESCALATE: amend`), hands the ticket back with no code |
| `feature` | `specify` -> `plan` -> `tasks` -> `implement` into a new `specs/<next number>-<slug>/`; each stage's artifact is verified before the next |
| `amend` | `spec` -> `plan` -> `tasks` -> `implement` on the existing spec folder; new tasks are tagged `[KEY]` and only those are implemented |
| `task` | the original single-shot minimal change |

An amend with no resolvable spec is handed back to the ticket with a question instead of guessing. A bugfix that escalates is handed back too: the comment explains why and asks for the `bot-amend` label plus the spec folder, so a human decides before a wide spec change is made.

Tasks that only run tests, lint or typecheck (for example "Run quality gates (`npm run lint`)") never block the `implement` stage. The engine runs headless and cannot execute commands; the bot runs `check_commands` itself afterwards, and the prompts tell the agent not to write such tasks. When a stage is incomplete the engine's reply is logged as a warning.
Feature/amend runs may also write `spec_dir` and use `spec_max_diff_lines`; they then share the normal setup, checks, repair rounds, commit and draft PR.

## Editing prompts
Every engine prompt is a Jinja template in `src/dev_bot/prompts/` (`task.md`, `bugfix.md`, `routing.md`, `stages/*.md`, ...); `prompt.py` only passes in variables. Shared pieces (including the repo-skills block) are in `_partials.md`, and the Spec Kit stages extend `stages/_base.md`. Undefined variables raise instead of rendering blank. Tests and the fake engine identify prompts by their first words (`REPAIR`, `BUGFIX`, `SPECKIT PLAN`, ...), so keep those.

## Engines
- `antigravity`: `agy --mode accept-edits --sandbox --output-format json -p ...` (verified headless; needs AI credits).
- `claude`: `claude -p ... --permission-mode acceptEdits` (written from docs, **untested**, CLI not installed here).
