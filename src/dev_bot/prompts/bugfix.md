{% from "_partials.md" import ticket_note, ticket, rules, guidance %}
BUGFIX. You are fixing the defect in Jira ticket {{ issue.key }} in this repository{% if guide.bugfix_skill %}, following the repo's bugfix method ({{ guide.bugfix_skill.path }}; read it first){% endif %}.
{{ ticket_note() }}

{{ ticket(issue) }}

{% if agents_md | trim %}
Repository conventions (AGENTS.md):
{{ agents_md | trim }}

{% endif %}
{% set repo_guidance = guidance(guide, repo) | trim %}
{% if repo_guidance %}
{{ repo_guidance }}

{% endif %}
Method:
{% if repo.spec_aware %}
{% if spec_hint %}
- The affected feature probably lives in `{{ spec_hint }}`; check its spec.md first.
{% endif %}
- Find the root cause, then classify the defect:
  A = pure code defect (spec is right): add a failing regression test at the seam, then the minimal fix.
  B = stale spec (spec disagrees with intended behavior): update spec.md first with a short revision note, align contracts and tests, then the code.
  C = underspecified boundary: append the missing scenario to the spec's edge cases, add a boundary test, then the guard.
  D = the fix would break a contract/invariant or the constitution, or needs a product decision: change NOTHING and explain the trade-offs.
- A regression test is mandatory for A, B and C.
- ESCALATE: if the defect is B or C and the spec change is wide (many requirements, plan/tasks/contracts must change, `[NEEDS CLARIFICATION]` markers, missing artifacts), it is an amendment, not a bugfix. Change NOTHING, explain which spec and why, and put the line `ESCALATE: amend` before the final `CLASS:` line.
{% else %}
- Find the root cause, then classify the defect:
  A = code defect: add a failing regression test at the seam, then the minimal fix.
  D = the fix would break a documented contract or invariant, or needs a product decision: change NOTHING and explain the trade-offs.
- A regression test is mandatory for A.
{% endif %}
{{ rules(repo) }}
- You cannot run commands; the bot runs the repo's checks afterwards, so write lint-clean, typed code.
{% if repo.spec_aware %}
- Finish with a short plain-text report (root cause, spec changes, code changes, regression test) whose LAST line is exactly `CLASS: A`, `CLASS: B`, `CLASS: C` or `CLASS: D`.
{%- else %}
- Finish with a short plain-text report (root cause, code changes, regression test) whose LAST line is exactly `CLASS: A` or `CLASS: D`.
{%- endif %}
