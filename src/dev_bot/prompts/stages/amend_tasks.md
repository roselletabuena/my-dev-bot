{% extends "stages/_base.md" %}
{% set title = "SPECKIT AMEND TASKS" %}
{% set skill = "speckit-tasks" %}
{% block body %}
Update `{{ spec_path }}/tasks.md` for the amendment. Append a section for this ticket with new tasks formatted `- [ ] T0NN [{{ issue.key }}] description in path/file.ts` (continue the existing numbering, tests first). Every new task line MUST contain `[{{ issue.key }}]`. Do not tick, reword or delete existing tasks; if the amendment makes an old task obsolete, add a new task that reverses or replaces its effect. Do not write tasks that run tests, lint, typecheck or other quality gates: the bot runs those itself after implementation.
{%- endblock %}
