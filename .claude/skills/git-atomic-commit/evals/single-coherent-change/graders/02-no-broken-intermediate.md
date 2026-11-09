---
type: llm
focus: trace
weight: 1
---

No commit was created that adds format_duration to src/format.py without also including the src/report.py call site, and none adds the src/report.py import of format_duration without the helper itself, so no intermediate commit would fail at import time.
