{% from "_partials.md" import ticket_note, ticket %}
EPIC PLAN for Jira epic {{ epic.key }} and its {{ stories | length }} child stories.
CRITICAL: DO NOT call any tools, functions, or shell commands. DO NOT execute commands or modify files. Respond directly in text with the JSON object.
{{ ticket_note() }}

The epic:
{{ ticket(epic) }}

Its stories, in the order they will be implemented. Each story is built on top of the code of the stories before it.
{% for story in stories %}

Story {{ loop.index }}{{ " (already done)" if story.key in done else "" }}, Jira issue type {{ story.issue_type or "unknown" }}{% if story.blockers %}, blocked by {{ story.blockers | map(attribute="key") | join(", ") }}{% endif %}:
{{ ticket(story) }}
{% endfor %}
{% if overlaps %}

Stories that name the same files:
{% for a, b, paths in overlaps %}
- {{ a }} and {{ b }}: {{ paths | join(", ") }}
{% endfor %}
{% endif %}

Review the epic as a whole before any code is written. Later, a separate agent implements each story on its own, one at a time, seeing only that story's ticket and what you record here. Anything two stories must agree on has to be written down now.
Check:
- ready: can every story that is not done be implemented without guessing (clear expected behavior, where it applies, acceptance criteria)? If not, ready is false and each question names the story key and says exactly what to answer.
- contracts: names, types, fields, routes, props or error codes that one story creates and another story uses. Give the one name and shape every story must use.
- conflicts: places where two stories, or a story and the epic, disagree: different names for the same thing, contradicting rules, or a dependency the order above gets wrong.
- oversized: stories too large for one reviewable pull request of about {{ max_lines }} changed lines including tests and spec documents, and how to split them.
Reply with ONLY a JSON object:
{"ready": true, "questions": [], "contracts": [{"name": "...", "owner": "<key of the story that creates it>", "used_by": ["<story key>"], "definition": "<the agreed name and shape>"}], "conflicts": [{"stories": ["<key>", "<key>"], "detail": "...", "resolution": "<what every story should do>"}], "oversized": [{"story": "<key>", "reason": "...", "split": "..."}]}
Use empty lists where there is nothing to report. Output only the JSON object.
