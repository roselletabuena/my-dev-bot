{% from "_partials.md" import ticket_note, ticket %}
PR DESCRIPTION. Do not modify any files and do not run any commands.
{{ ticket_note() }}

{{ ticket(issue) }}
Ticket link: {{ jira_base_url }}/browse/{{ issue.key }}
Parent epic: {% if issue.parent_key %}{{ issue.parent_key }}: {{ issue.parent_summary }} ({{ jira_base_url }}/browse/{{ issue.parent_key }}){% else %}none (this ticket has no parent epic){% endif %}


<template>
{{ template | trim }}
</template>

<diff>
{{ diff | trim }}
</diff>

Fill in the pull request template above for this change and reply with ONLY the completed markdown: no code fence around it and no commentary.
Rules:
- Keep every heading, emoji, section order and horizontal rule exactly as in the template.
- Jira section: replace the VS-XX placeholders with the real parent epic and this ticket (key, title and link). If there is no parent epic, write 'None'.
- Executive Summary: 2-4 sentences covering the problem or user story and what this PR does.
- Key Architectural & Code Changes: describe only what the diff shows. Name the real directories and files touched (do not leave `<slice>` placeholders), and delete empty placeholder bullets.
- Do NOT tick any checkbox, and leave the Testing & Verification checkboxes and its ```text block exactly as they are: the bot fills them from real results.
- Leave the whole Constitution Compliance checklist unticked; the human reviewer confirms it.
- Visual Evidence: write 'N/A - Non-UI change' when the diff touches no UI files.
- Do not invent anything that the ticket or diff does not support.
