ATOMIC COMMITS

You are splitting a verified set of changes for Jira ticket {{ issue.key }} ({{ issue.summary }}) into logical, single-purpose atomic git commits.

Changed files (every file MUST appear in exactly one commit):
{% for f in files %}
- {{ f }}
{% endfor %}

Rules:
- Group files by logical purpose (e.g., schema/migrations, validation/types, backend service/API, UI components, tests, docs) in dependency order so each commit makes sense on its own.
- Use concise imperative commit messages (e.g., `feat(media): add focal-point schema fields`, `test(media): add upload validation tests`). Do NOT include `Co-authored-by` trailers; the bot adds them automatically.
- Every file in the list above must be included in exactly one commit, using the exact relative path. Do not invent paths.
- Return ONLY a JSON object with this exact shape and no extra prose:
  {"commits": [{"message": "...", "files": ["path/to/file1", "path/to/file2"]}]}

Diff:
```diff
{{ diff }}
```
