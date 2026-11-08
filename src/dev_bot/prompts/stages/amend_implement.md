{% extends "stages/_base.md" %}
{% set title = "SPECKIT AMEND IMPLEMENT" %}
{% set skill = "speckit-implement" %}
{% block body %}
{% include "stages/_conventions.md" %}
Implement ONLY the tasks in `{{ spec_path }}/tasks.md` that contain `[{{ issue.key }}]`, in order, test-first, changing the existing code minimally. Change each finished task's `- [ ]` to `- [x]`. Do not touch other tasks, even if they are unticked. If a task only runs tests, lint or typecheck, skip it: the bot runs those after you finish.
{%- endblock %}
