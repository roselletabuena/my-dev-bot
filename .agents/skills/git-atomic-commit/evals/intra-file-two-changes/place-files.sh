#!/usr/bin/env bash
# One file holds two unrelated edits, far apart. Whole-file staging cannot separate
# them, so this exercises the intra-file split path. The repo log uses Conventional
# Commits here, so matching the local style means adopting that prefix.
set -eu

git init -q .
git config user.email dev@example.com
git config user.name "Dana Reyes"
git config commit.gpgsign false

mkdir -p src

cat > src/handlers.py <<'EOF'
import logging

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 5


def fetch_user(client, user_id):
    log.info("fetching user %s", user_id)
    return client.get(f"/users/{user_id}", timeout=REQUEST_TIMEOUT)


def fetch_orders(client, user_id):
    log.info("fetching orders for user %s", user_id)
    return client.get(f"/users/{user_id}/orders", timeout=REQUEST_TIMEOUT)


def close(client):
    log.info("closeing client conection")
    client.close()
EOF

git add -A
git commit -q -m "feat(handlers): add user and order fetch handlers"

cat > README.md <<'EOF'
# service

Internal API handlers.
EOF
git add -A
git commit -q -m "docs: add service readme"

sed -i 's/^REQUEST_TIMEOUT = 5$/REQUEST_TIMEOUT = 5  # seconds/' src/handlers.py
git add -A
git commit -q -m "style(handlers): annotate timeout units"

# ---------- dirty tree: two unrelated edits in ONE file ----------
# (1) bug fix near the top: upstream is slow, 5s causes spurious timeouts
# (2) unrelated typo fix in a log message near the bottom
cat > src/handlers.py <<'EOF'
import logging

log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds


def fetch_user(client, user_id):
    log.info("fetching user %s", user_id)
    return client.get(f"/users/{user_id}", timeout=REQUEST_TIMEOUT)


def fetch_orders(client, user_id):
    log.info("fetching orders for user %s", user_id)
    return client.get(f"/users/{user_id}/orders", timeout=REQUEST_TIMEOUT)


def close(client):
    log.info("closing client connection")
    client.close()
EOF
