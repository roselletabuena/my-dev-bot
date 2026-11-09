#!/usr/bin/env bash
# The log is all legacy plain prose, but an active commit-msg hook enforces
# Conventional Commits. Inferring style from the log alone gets the commit REJECTED.
# The skill says a hook overrides inference from the log; this checks that.
set -eu

git init -q .
git config user.email dev@example.com
git config user.name "Dana Reyes"
git config commit.gpgsign false

mkdir -p src tests

cat > src/api.py <<'EOF'
DEFAULT_PAGE_SIZE = 25


def paginate(rows, page):
    start = page * DEFAULT_PAGE_SIZE
    return rows[start:start + DEFAULT_PAGE_SIZE]
EOF

cat > src/serializers.py <<'EOF'
def serialize_user(user):
    return {"id": user["id"], "name": user["name"]}
EOF

cat > tests/test_api.py <<'EOF'
from src.api import paginate


def test_first_page():
    assert paginate(list(range(100)), 0)[0] == 0
EOF

cat > requirements.txt <<'EOF'
django==4.2.1
djangorestframework==3.14.0
EOF

git add -A
git commit -q -m "Add pagination and user serializer"
git commit -q --allow-empty -m "Wire up the staging deploy script"
git commit -q --allow-empty -m "Fix pagination offset on the last page"

# The hook is installed AFTER the legacy history, which is why the log and the
# hook disagree - the team adopted Conventional Commits recently.
cat > .git/hooks/commit-msg <<'HOOKEOF'
#!/bin/sh
subject=$(head -n 1 "$1")
if ! printf '%s' "$subject" | grep -qE '^(feat|fix|chore|docs|style|refactor|test|perf)(\([a-z0-9._-]+\))?: .+'; then
  echo "" >&2
  echo "commit-msg hook: rejected." >&2
  echo "  Subject must follow Conventional Commits, e.g. 'fix(api): correct offset'." >&2
  echo "  Got: $subject" >&2
  exit 1
fi
HOOKEOF
chmod +x .git/hooks/commit-msg

cat > CONTRIBUTING.md <<'EOF'
# Contributing

Commit subjects must follow Conventional Commits. This is enforced by a
`commit-msg` hook. Older commits predate the rule and were not rewritten.
EOF
git add -A
git commit -q --no-verify -m "Add contributing guide"

# ---------- dirty tree: two unrelated concerns ----------

# (1) bug fix: last page returned an empty slice for exact multiples
cat > src/api.py <<'EOF'
DEFAULT_PAGE_SIZE = 25


def paginate(rows, page):
    start = page * DEFAULT_PAGE_SIZE
    if start >= len(rows) and rows:
        return []
    return rows[start:start + DEFAULT_PAGE_SIZE]
EOF

# (2) unrelated dependency bump
cat > requirements.txt <<'EOF'
django==4.2.16
djangorestframework==3.15.2
EOF
