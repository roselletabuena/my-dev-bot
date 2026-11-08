{% from "_partials.md" import ticket_note, ticket %}
ROUTING. Do not modify any files.
{{ ticket_note() }}

{{ ticket(issue) }}
Jira issue type: {{ issue.issue_type or "unknown" }}
{% if issue.parent_key %}
Parent epic: {{ issue.parent_key }}: {{ issue.parent_summary }}
{% endif %}

Existing Spec Kit specs in the repository:
{% for spec in specs %}
- {{ spec.folder }}: {{ spec.title }}{{ (" (ticket " ~ spec.jira_key ~ ")") if spec.jira_key else "" }}
{% else %}
(none)
{% endfor %}

Pick the workflow for this ticket:
- bugfix: something that should already work is broken.
- amend: changes, extends or removes behavior that an existing spec above already describes.
- feature: new user-facing capability that no existing spec covers and that deserves its own spec.
- task: small chore or tweak (copy, config, refactor, tiny UI change) that needs no spec.
Reply with ONLY a JSON object: {"workflow": "bugfix|amend|feature|task", "spec": "<existing folder name, required for amend, else null>", "reason": "<one sentence>"}.
