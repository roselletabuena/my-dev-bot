{#- Common frame of every Spec Kit stage. A stage template sets `title` and `skill`, then fills `body`. -#}
{% from "_partials.md" import ticket_note, ticket, rules %}
{{ title }}. One stage of a Spec Kit run for Jira ticket {{ issue.key }}; files from earlier stages are already in the working tree. Read and follow `.agents/skills/{{ skill }}/SKILL.md` for this stage's structure.
{{ ticket_note() }}

{{ ticket(issue) }}

{% block body %}{% endblock %}

Rules:
- You can only read and edit files: you cannot run commands, scripts, git or tests. Create files directly from the templates in `.specify/templates/` instead of running `.specify/scripts`.
- Nobody can answer questions. Where Spec Kit would ask for clarification, choose the most reasonable default and record it in the document under Assumptions.
- Do not create branches or worktrees, commit, push, or post to Jira or GitHub: the bot does that.
- Follow .specify/memory/constitution.md. Where a skill file mentioned above conflicts with these rules, these rules win.
{{ rules(repo) }}
- When done, reply with a short plain-text summary.
