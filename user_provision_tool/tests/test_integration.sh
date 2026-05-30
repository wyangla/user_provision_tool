#!/usr/bin/env bash
# tests/test_integration.sh
# Integration tests for the user provision API.
# Builds the Docker image, starts the service, exercises all endpoints,
# then tears everything down.
#
# Usage:
#   cd <project-root>
#   bash tests/test_integration.sh
#
# Requirements:
#   - docker / docker compose
#   - curl
#   - jq  (optional; falls back to python3 for JSON parsing)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
API_PORT="${PROVISION_API_PORT:-8765}"
API_URL="http://localhost:${API_PORT}"
COMPOSE_FILE="$REPO_DIR/docker-compose.provision.yml"

# Test user / service — used to build the container name filter
TEST_USER="testuser"
TEST_SVC="myapp"
TEST_LABEL="0"
# Matches the provision stack containers and the user-provisioned containers
CONTAINER_FILTER="provision-api|provision-nginx|${TEST_SVC}-user_${TEST_USER}-${TEST_LABEL}"

# Colours
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASS=0
FAIL=0
_torn_down=0

pass() { echo -e "${GREEN}PASS${NC} $*"; ((PASS++)) || true; }
fail() { echo -e "${RED}FAIL${NC} $*"; ((FAIL++)) || true; }
die()  { echo -e "${RED}FATAL${NC} $*" >&2; exit 1; }

# Print a table of running containers matching CONTAINER_FILTER
print_containers() {
    local label="${1:-}"
    echo "  [running containers${label:+ — $label}]"
    local header rows
    header=$(docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" 2>/dev/null | head -1) || true
    rows=$(docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" 2>/dev/null | tail -n +2 \
        | grep -E "$CONTAINER_FILTER") || true
    if [ -n "$rows" ]; then
        printf "    %s\n" "$header"
        echo "$rows" | awk '{printf "    %s\n", $0}'
    else
        echo "    (none)"
    fi
}

# Print a table of ALL containers (including stopped) matching CONTAINER_FILTER
print_all_containers() {
    local label="${1:-}"
    echo "  [all containers${label:+ — $label}]"
    local header rows
    header=$(docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" 2>/dev/null | head -1) || true
    rows=$(docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" 2>/dev/null | tail -n +2 \
        | grep -E "$CONTAINER_FILTER") || true
    if [ -n "$rows" ]; then
        printf "    %s\n" "$header"
        echo "$rows" | awk '{printf "    %s\n", $0}'
    else
        echo "    (none)"
    fi
}

# ---------------------------------------------------------------------------
# JSON helper (prefers jq, falls back to python3)
# ---------------------------------------------------------------------------
jq_or_python() {
    local filter="$1"
    local json="$2"
    if command -v jq &>/dev/null; then
        echo "$json" | jq -r "$filter"
    else
        python3 -c "
import sys, json
data = json.loads('''$json''')
# Simple key path evaluation: .foo.bar or .foo[0].bar
keys = '$filter'.lstrip('.').split('.')
val = data
for k in keys:
    if k.endswith(']'):
        k, idx = k.rstrip(']').split('[')
        val = val[k][int(idx)]
    else:
        val = val[k]
print(val)
"
    fi
}

# ---------------------------------------------------------------------------
# Setup: temp provision dir
# ---------------------------------------------------------------------------
export PROVISION_DIR
PROVISION_DIR="$(mktemp -d)"
echo "PROVISION_DIR=$PROVISION_DIR"

mkdir -p \
    "$PROVISION_DIR/generated" \
    "$PROVISION_DIR/templates" \
    "$PROVISION_DIR/user-data/testuser/app" \
    "$PROVISION_DIR/user-data/testuser/db"

cp "$SCRIPT_DIR/fixtures/docker-compose.template.yml.j2" "$PROVISION_DIR/templates/"
cp "$SCRIPT_DIR/fixtures/myapp.template.nginx.conf.j2"  "$PROVISION_DIR/templates/" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Teardown function
# ---------------------------------------------------------------------------
teardown() {
    [ "$_torn_down" -eq 1 ] && return
    _torn_down=1
    echo ""
    echo "--- Teardown ---"
    print_all_containers "before cleanup"
    (cd "$REPO_DIR" && docker compose -f "$COMPOSE_FILE" down -v 2>/dev/null) || true
    rm -rf "$PROVISION_DIR"
    echo "Cleaned up."
}
trap teardown EXIT

# ---------------------------------------------------------------------------
# Build & start the provision API container
# ---------------------------------------------------------------------------
echo ""
echo "--- Building and starting provision-api ---"
cd "$REPO_DIR"
docker compose -f "$COMPOSE_FILE" up -d --build

# Wait for API to be ready (up to 60 s) using /health (no docker call needed)
echo "Waiting for API at $API_URL ..."
for i in $(seq 1 60); do
    if curl -sf "$API_URL/health" -o /dev/null 2>/dev/null; then
        echo "API ready after ${i}s"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "--- provision-api container logs ---"
        docker logs "$(docker compose -f "$COMPOSE_FILE" ps -q provision-api 2>/dev/null)" 2>/dev/null || true
        die "API did not become ready within 60 seconds"
    fi
    sleep 1
done
print_containers "provision-api started"

# ---------------------------------------------------------------------------
# Test 1: GET /users returns empty list initially
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 1: GET /users (empty) ---"
resp=$(curl -sf "$API_URL/users")
count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['user_status']))" 2>/dev/null || echo "0")
if [ "$count" -eq 0 ]; then
    pass "GET /users returns empty list"
else
    fail "GET /users should return empty list, got: $resp"
fi

# ---------------------------------------------------------------------------
# Test 2: POST /users — register testuser/myapp
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 2: POST /users (register) ---"
REGISTER_BODY=$(cat <<EOF
{
  "user_name": "testuser",
  "service_name": "myapp",
  "compose_template_path": "${PROVISION_DIR}/templates/docker-compose.template.yml.j2",
  "nginx_conf_template_path": null,
  "label": "0",
  "domain": "localhost",
  "passwd": "s3cr3t",
  "volumes": {
    "app_data": "${PROVISION_DIR}/user-data/testuser/app",
    "db_data":  "${PROVISION_DIR}/user-data/testuser/db"
  }
}
EOF
)

reg_resp=$(curl -sf -X POST "$API_URL/users" \
    -H "Content-Type: application/json" \
    -d "$REGISTER_BODY")

reg_status=$(echo "$reg_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$reg_status" = "registered" ]; then
    pass "POST /users registered testuser/myapp/0"
else
    fail "POST /users failed: $reg_resp"
fi

# ---------------------------------------------------------------------------
# Test 3: POST /users duplicate returns 409
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 3: POST /users duplicate (expect 409) ---"
dup_http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL/users" \
    -H "Content-Type: application/json" \
    -d "$REGISTER_BODY")
if [ "$dup_http" = "409" ]; then
    pass "Duplicate registration returns 409"
else
    fail "Expected 409 for duplicate, got: $dup_http"
fi

# ---------------------------------------------------------------------------
# Test 4: Containers are actually running
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 4: Verify containers started ---"
EXPECTED_WEB="myapp-user_testuser-0-web"
EXPECTED_DB="myapp-user_testuser-0-db"

# Allow a few seconds for containers to start
for i in $(seq 1 20); do
    running=$(docker ps --format '{{.Names}}')
    web_ok=$(echo "$running" | grep -c "$EXPECTED_WEB" || true)
    db_ok=$(echo "$running"  | grep -c "$EXPECTED_DB"  || true)
    if [ "$web_ok" -ge 1 ] && [ "$db_ok" -ge 1 ]; then
        break
    fi
    sleep 2
done
print_containers "after registration"

if [ "$web_ok" -ge 1 ]; then
    pass "Container $EXPECTED_WEB is running"
else
    fail "Container $EXPECTED_WEB not found in docker ps"
fi

if [ "$db_ok" -ge 1 ]; then
    pass "Container $EXPECTED_DB is running"
else
    fail "Container $EXPECTED_DB not found in docker ps"
fi

# ---------------------------------------------------------------------------
# Test 5: GET /users/testuser shows healthy service
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 5: GET /users/testuser (healthy) ---"
status_resp=$(curl -sf "$API_URL/users/testuser")
healthy_count=$(echo "$status_resp" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d['user_status'][0]['summary']['healthy_services_#'])" 2>/dev/null || echo "0")
if [ "$healthy_count" -ge 1 ]; then
    pass "GET /users/testuser reports $healthy_count healthy service(s)"
else
    fail "Expected healthy_services_# >= 1, got: $status_resp"
fi

# ---------------------------------------------------------------------------
# Test 6: POST .../rebuild  (no_cache=false)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 6: POST /users/testuser/services/myapp/0/rebuild ---"
rebuild_resp=$(curl -sf -X POST "$API_URL/users/testuser/services/myapp/0/rebuild" \
    -H "Content-Type: application/json" \
    -d '{"no_cache": false}')
rebuild_status=$(echo "$rebuild_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$rebuild_status" = "rebuilt" ]; then
    pass "POST .../rebuild returned status=rebuilt"
else
    fail "Rebuild failed: $rebuild_resp"
fi

# ---------------------------------------------------------------------------
# Test 7: Containers still running after rebuild
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 7: Containers still running after rebuild ---"
for i in $(seq 1 15); do
    running=$(docker ps --format '{{.Names}}')
    web_ok=$(echo "$running" | grep -c "$EXPECTED_WEB" || true)
    db_ok=$(echo "$running"  | grep -c "$EXPECTED_DB"  || true)
    if [ "$web_ok" -ge 1 ] && [ "$db_ok" -ge 1 ]; then
        break
    fi
    sleep 2
done
print_containers "after rebuild"

if [ "$web_ok" -ge 1 ] && [ "$db_ok" -ge 1 ]; then
    pass "Containers still running after rebuild"
else
    fail "Containers not running after rebuild"
fi

# ---------------------------------------------------------------------------
# Test 8: DELETE /users/testuser/services/myapp/0
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 8: DELETE /users/testuser/services/myapp/0 ---"
del_resp=$(curl -sf -X DELETE "$API_URL/users/testuser/services/myapp/0")
del_status=$(echo "$del_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$del_status" = "removed" ]; then
    pass "DELETE returned status=removed"
else
    fail "DELETE failed: $del_resp"
fi

# ---------------------------------------------------------------------------
# Test 9: Containers are gone after removal
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 9: Containers gone after removal ---"
sleep 3
print_all_containers "after removal"
running=$(docker ps --format '{{.Names}}')
web_gone=$(echo "$running" | grep -c "$EXPECTED_WEB" || true)
db_gone=$(echo "$running"  | grep -c "$EXPECTED_DB"  || true)
if [ "$web_gone" -eq 0 ]; then
    pass "Container $EXPECTED_WEB is gone"
else
    fail "Container $EXPECTED_WEB still running after removal"
fi
if [ "$db_gone" -eq 0 ]; then
    pass "Container $EXPECTED_DB is gone"
else
    fail "Container $EXPECTED_DB still running after removal"
fi

# ---------------------------------------------------------------------------
# Test 10: GET /users returns empty again after removal
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 10: GET /users empty after removal ---"
resp=$(curl -sf "$API_URL/users")
count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['user_status']))" 2>/dev/null || echo "0")
if [ "$count" -eq 0 ]; then
    pass "GET /users returns empty after removal"
else
    fail "GET /users should be empty after removal, got: $resp"
fi

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
echo ""
echo "================================"
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "================================"

[ "$FAIL" -eq 0 ] || exit 1
