#!/usr/bin/env bash
# The tree spans three files but holds exactly ONE logical change: a new helper, the
# call site that uses it, and its test. Splitting this would leave intermediate
# commits that are incomplete or broken. The correct answer is a single commit.
set -eu

git init -q .
git config user.email dev@example.com
git config user.name "Dana Reyes"
git config commit.gpgsign false

mkdir -p src tests

cat > src/format.py <<'EOF'
def format_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}TB"
EOF

cat > src/report.py <<'EOF'
from src.format import format_bytes


def render(job):
    return f"{job['name']}: {format_bytes(job['size'])}"
EOF

cat > tests/test_format.py <<'EOF'
from src.format import format_bytes


def test_format_bytes():
    assert format_bytes(2048) == "2KB"
EOF

git add -A
git commit -q -m "Add byte formatting helper for job reports"

cat > .gitignore <<'EOF'
__pycache__/
EOF
git add -A
git commit -q -m "Ignore python cache directories"

# ---------- dirty tree: ONE logical change across three files ----------
# A duration helper, the call site that uses it, and its test. Each file alone is
# an incomplete change: the helper unused, or the call site referencing nothing.

cat > src/format.py <<'EOF'
def format_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}TB"


def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes:.0f}m{seconds:.0f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours:.0f}h{minutes:.0f}m"
EOF

cat > src/report.py <<'EOF'
from src.format import format_bytes, format_duration


def render(job):
    return (
        f"{job['name']}: {format_bytes(job['size'])} "
        f"in {format_duration(job['elapsed'])}"
    )
EOF

cat > tests/test_format.py <<'EOF'
from src.format import format_bytes, format_duration


def test_format_bytes():
    assert format_bytes(2048) == "2KB"


def test_format_duration():
    assert format_duration(45) == "45s"
    assert format_duration(90) == "1m30s"
    assert format_duration(3700) == "1h1m"
EOF
