{% extends "stages/_base.md" %}
{% set title = "SPECKIT TASKS" %}
{% set skill = "speckit-tasks" %}
{% block body %}
Write `{{ spec_path }}/tasks.md`: dependency-ordered checklist lines `- [ ] T001 [P] description in path/file.ts`, tests before the code they cover, grouped by user story. Do not write tasks that run tests, lint, typecheck or other quality gates: the bot runs those itself after implementation.
Then do a consistency pass (see .agents/skills/speckit-analyze/SKILL.md): every requirement in `{{ spec_path }}/spec.md` must map to a task and nothing in plan.md may be contradicted. Fix gaps in the documents.
{%- endblock %}
