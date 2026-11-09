---
tags: [git-atomic-commit]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Edit, Bash]
---

the staging upstream kept timing out so I raised the timeout in handlers.py, and while I was in there I fixed a typo in one of the log lines. can you commit those separately? I want the timeout change on its own so I can revert it easily if 30s turns out to be too aggressive
