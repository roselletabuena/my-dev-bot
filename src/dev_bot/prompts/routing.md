{% from "_partials.md" import ticket_note, ticket %}
ROUTING for Jira ticket {{ issue.key }}.
CRITICAL: DO NOT call any tools, functions, or shell commands. DO NOT execute any commands or modify any files. Respond directly in text with the JSON object.
{{ ticket_note() }}

{{ ticket(issue) }}
Jira issue type: {{ issue.issue_type or "unknown" }}
{% if issue.parent_key %}
Parent epic: {{ issue.parent_key }}: {{ issue.parent_summary }}
{% endif %}

{% if "amend" in workflows %}
Existing Spec Kit specs in the repository:
{% for spec in specs %}
- {{ spec.folder }}: {{ spec.title }}{{ (" (ticket " ~ spec.jira_key ~ ")") if spec.jira_key else "" }}
{% else %}
(none)
{% endfor %}

{% endif %}
Pick the workflow for this ticket:
{% if "bugfix" in workflows %}
- bugfix: something that should already work is broken.
{% endif %}
{% if "amend" in workflows %}
- amend: changes, extends or removes behavior that an existing spec above already describes.
{% endif %}
{% if "feature" in workflows %}
- feature: new user-facing capability that no existing spec covers and that deserves its own spec.
{% endif %}
{% if "task" in workflows %}
- task: {{ "small chore or tweak (copy, config, refactor, tiny UI change) that needs no spec." if "feature" in workflows or "amend" in workflows else "any change that is not a bug fix: a new component, a chore, a tweak, a refactor." }}
{% endif %}
Reply with ONLY a JSON object: {"workflow": "{{ workflows | join('|') }}"{% if "amend" in workflows %}, "spec": "<existing folder name, required for amend, else null>"{% endif %}, "reason": "<one sentence>"}.
