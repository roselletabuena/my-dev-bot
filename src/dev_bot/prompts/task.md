{% from "_partials.md" import ticket_note, ticket, rules %}
You are implementing Jira ticket {{ issue.key }} in this repository.
{{ ticket_note() }}

{{ ticket(issue) }}

{% if agents_md | trim %}
Repository conventions (AGENTS.md):
{{ agents_md | trim }}

{% endif %}
Rules:
- Make the smallest change that satisfies the ticket; add or update a unit test where sensible.
{{ rules(repo) }}
{% if repo.check_commands %}
- After you finish, the bot runs these checks and your change must pass them all (you cannot run them yourself, so write lint-clean, type-safe, formatted code):
{% for command in repo.check_commands %}
  {{ command }}
{% endfor %}
{% endif %}
- When done, reply with a short plain-text summary of what you changed and why.
