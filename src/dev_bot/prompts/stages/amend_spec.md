{% extends "stages/_base.md" %}
{% set title = "SPECKIT AMEND SPEC" %}
{% set skill = "speckit-specify" %}
{% block body %}
{% include "stages/_conventions.md" %}
This ticket CHANGES an existing feature. Update `{{ spec_path }}/spec.md` in place:
- Edit only what the ticket changes, plus the acceptance criteria and edge cases it affects; keep unrelated sections word-for-word.
- Add a revision-history entry naming {{ issue.key }} ({{ jira_url }}) and what changed.
- Bring `{{ spec_path }}/research.md` and `{{ spec_path }}/checklists/requirements.md` in line where they are affected.
Do not create a new spec folder.
{%- endblock %}
