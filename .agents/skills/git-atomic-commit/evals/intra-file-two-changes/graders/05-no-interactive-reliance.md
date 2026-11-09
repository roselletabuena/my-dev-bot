---
type: llm
focus: trace
weight: 1
---

The split was not accomplished by relying on an interactive git command. If git add -p, git add -i, git commit -p or git checkout -p was attempted, its silent no-op was detected and a non-interactive technique was used instead rather than its exit code being trusted.
