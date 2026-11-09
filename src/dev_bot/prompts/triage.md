{% from "_partials.md" import ticket_note, ticket %}
READINESS CHECK for Jira ticket {{ issue.key }}.
CRITICAL: DO NOT call any tools, functions, or shell commands. DO NOT execute any commands or modify any files. Respond directly in text with the JSON object.
{{ ticket_note() }}

{{ ticket(issue) }}

Decide whether this ticket has enough detail (clear expected behavior, where it applies, acceptance criteria) for a developer to implement without guessing.
Reply with ONLY a JSON object: {"ready": true, "questions": []} or {"ready": false, "questions": ["specific question", ...]}. If ready is true, questions must be empty.
Output the raw JSON object now. Do not output greetings, commentary, or conversational text.
