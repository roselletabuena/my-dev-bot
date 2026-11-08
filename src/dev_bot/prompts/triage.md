{% from "_partials.md" import ticket_note, ticket %}
READINESS CHECK. Do not modify any files.
{{ ticket_note() }}

{{ ticket(issue) }}

Decide whether this ticket has enough detail (clear expected behavior, where it applies, acceptance criteria) for a developer to implement without guessing. Reply with ONLY a JSON object: {"ready": true|false, "questions": ["specific question", ...]}. If ready is true, questions must be empty.
