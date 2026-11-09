#!/usr/bin/env bash
# Two unrelated changes land on CONSECUTIVE lines inside one function, so git diff
# emits them as a SINGLE hunk. Hunk-level slicing cannot separate them - the only
# routes are editing the patch body line by line, or the rebuild fallback.
set -eu

git init -q .
git config user.email dev@example.com
git config user.name "Dana Reyes"
git config commit.gpgsign false

mkdir -p src

cat > src/pricing.py <<'EOF'
import logging

log = logging.getLogger(__name__)


def compute_total(items):
    return sum(i["price"] * i["qty"] for i in items)


def apply_discount(order):
    total = compute_total(order["items"])
    rate = 0.10
    log.debug("discount")
    discount = total * rate
    return total - discount
EOF

git add -A
git commit -q -m "Add pricing helpers"

cat > src/__init__.py <<'EOF'
EOF
git add -A
git commit -q -m "Make src a package"

# ---------- dirty tree: two unrelated edits on ADJACENT lines ----------
# (1) bug fix: loyalty customers were never getting their 20% rate
# (2) unrelated observability change: the debug log carried no detail
cat > src/pricing.py <<'EOF'
import logging

log = logging.getLogger(__name__)


def compute_total(items):
    return sum(i["price"] * i["qty"] for i in items)


def apply_discount(order):
    total = compute_total(order["items"])
    rate = 0.20 if order.get("loyalty") else 0.10
    log.debug("applying discount rate %s to order %s", rate, order["id"])
    discount = total * rate
    return total - discount
EOF
