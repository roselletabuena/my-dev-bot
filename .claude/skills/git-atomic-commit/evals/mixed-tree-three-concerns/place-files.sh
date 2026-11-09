#!/usr/bin/env bash
# Builds a repo whose log uses a distinctive non-Conventional style, then leaves a
# working tree holding three unrelated concerns plus a test belonging to one of them.
set -eu

git init -q .
git config user.email dev@example.com
git config user.name "Dana Reyes"
git config commit.gpgsign false

mkdir -p src tests

# ---------- baseline, committed ----------
cat > src/cart.py <<'EOF'
TAX_RATE = 0.08


def subtotal(items):
    return sum(i["price"] * i["qty"] for i in items)


def total(items):
    return subtotal(items) * (1 + TAX_RATE)
EOF

cat > src/utils.py <<'EOF'
import json
def load_config(path):
    with open(path) as f:
        return json.load(f)
def slugify(text):
    return text.lower().replace(' ', '-')
EOF

cat > tests/test_cart.py <<'EOF'
from src.cart import subtotal


def test_subtotal():
    assert subtotal([{"price": 10, "qty": 2}]) == 20
EOF

cat > requirements.txt <<'EOF'
flask==2.2.0
requests==2.28.1
EOF

git add -A
git commit -q -m "[WEB-101] Add cart subtotal and tax helpers"

echo "*.pyc" > .gitignore
git add -A
git commit -q -m "[WEB-104] Ignore compiled python files"

cat >> src/cart.py <<'EOF'


def item_count(items):
    return sum(i["qty"] for i in items)
EOF
git add -A
git commit -q -m "[WEB-112] Add item count helper to cart"

# ---------- dirty working tree: three unrelated concerns ----------

# (1) real bug fix: tax was applied to discounted items incorrectly
cat > src/cart.py <<'EOF'
TAX_RATE = 0.08


def subtotal(items):
    return sum(i["price"] * i["qty"] for i in items)


def total(items):
    taxable = sum(
        i["price"] * i["qty"] for i in items if not i.get("tax_exempt")
    )
    return subtotal(items) + taxable * TAX_RATE


def item_count(items):
    return sum(i["qty"] for i in items)
EOF

# (2) test covering ONLY the bug fix above
cat > tests/test_cart.py <<'EOF'
from src.cart import subtotal, total


def test_subtotal():
    assert subtotal([{"price": 10, "qty": 2}]) == 20


def test_tax_exempt_items_are_not_taxed():
    items = [{"price": 100, "qty": 1, "tax_exempt": True}]
    assert total(items) == 100
EOF

# (3) pure reformatting, no behavior change at all
cat > src/utils.py <<'EOF'
import json


def load_config(path):
    with open(path) as f:
        return json.load(f)


def slugify(text):
    return text.lower().replace(" ", "-")
EOF

# (4) unrelated dependency bump
cat > requirements.txt <<'EOF'
flask==3.0.0
requests==2.31.0
EOF
