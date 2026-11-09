{% from "_partials.md" import ticket_note, ticket %}
READINESS CHECK for Jira ticket {{ issue.key }}.
CRITICAL: DO NOT call any tools, functions, or shell commands. DO NOT execute commands or modify files.
CRITICAL FORMAT RULE: DO NOT write markdown summaries, HTML badges, analysis, or headings. Your entire output must be ONLY a single JSON object.
{{ ticket_note() }}

{{ ticket(issue) }}

Decide whether this ticket has enough detail (clear expected behavior, where it applies, acceptance criteria) for a developer to implement without guessing.
Reply with ONLY a raw JSON object, starting with { and ending with }:
{"ready": true, "questions": []}
or
{"ready": false, "questions": ["specific question", ...]}

If ready is true, questions must be empty. Output the raw JSON object now. Do not output greetings, commentary, markdown headings, or conversational text.
CRITICAL: Output ONLY the raw JSON object starting with { and ending with }. Absolutely no other text, markdown formatting, or headings.
