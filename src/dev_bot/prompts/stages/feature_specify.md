{% extends "stages/_base.md" %}
{% set title = "SPECKIT SPECIFY" %}
{% set skill = "speckit-specify" %}
{% block body %}
{% include "stages/_conventions.md" %}
Write the specification for this ticket in `{{ spec_path }}/`:
- `{{ spec_path }}/spec.md`: header with Feature ID `{{ spec_folder }}` and `**Jira Key**: [{{ issue.key }}]({{ jira_url }})`, overview, actors, prioritized user stories, Given/When/Then acceptance criteria, edge cases, Assumptions.
- `{{ spec_path }}/research.md`: stack constraints and decisions with rationale.
- `{{ spec_path }}/checklists/requirements.md`: the quality checklist (accessibility, branding) for this feature.
Specify WHAT and WHY, not implementation details.
{%- endblock %}
