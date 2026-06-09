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
NGINX_PORT="${NGINX_HTTP_PORT:-8766}"
API_URL="http://localhost:${API_PORT}"
COMPOSE_FILE="$REPO_DIR/docker-compose.provision.yml"
export NGINX_HTTP_PORT="$NGINX_PORT"

# Test user / service — used to build the container name filter
TEST_USER="testuser"
TEST_SVC="myapp"
TEST_LABEL="0"
TEST_NETWORK_NAME="${TEST_SVC}-user_${TEST_USER}-${TEST_LABEL}"
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

# Print Docker networks matching TEST_NETWORK_NAME and show connected endpoints
print_networks() {
    local label="${1:-}"
    echo "  [user networks${label:+ — $label}]"
    local rows
    rows=$(docker network ls --format "{{.Name}}" 2>/dev/null \
        | grep -F "$TEST_NETWORK_NAME") || true
    if [ -n "$rows" ]; then
        while IFS= read -r net; do
            printf "    network: %s\n" "$net"
            docker network inspect "$net" \
                --format '    endpoints: {{range $k,$v := .Containers}}{{$v.Name}} {{end}}' \
                2>/dev/null || true
            echo
        done <<< "$rows"
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
    "$PROVISION_DIR/user-data/testuser/db" \
    "$PROVISION_DIR/user-data/fileuser/html" \
    "$PROVISION_DIR/user-data/fileuser/db"

cp "$SCRIPT_DIR/fixtures/docker-compose.template.yml.j2" "$PROVISION_DIR/templates/"
cp "$SCRIPT_DIR/fixtures/myapp.template.nginx.conf.j2"  "$PROVISION_DIR/templates/" 2>/dev/null || true
cp "$SCRIPT_DIR/fixtures/docker-compose.plain.yml"      "$PROVISION_DIR/templates/" 2>/dev/null || true
cp "$SCRIPT_DIR/fixtures/myapp.plain.nginx.conf"        "$PROVISION_DIR/templates/" 2>/dev/null || true

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

reg_resp=$(curl -sf -X POST "$API_URL/users?sync=true" \
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
dup_http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL/users?sync=true" \
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
print_networks "after registration"

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
rebuild_resp=$(curl -sf -X POST "$API_URL/users/testuser/services/myapp/0/rebuild?sync=true" \
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
print_networks "after rebuild"

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
del_resp=$(curl -sf -X DELETE "$API_URL/users/testuser/services/myapp/0?sync=true")
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
print_networks "after removal"
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
# Test 11: provision-nginx is connected to the user network after registration
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 11: provision-nginx connected to user network ---"
# Re-register to get a fresh network for the connectivity check
reg_resp2=$(curl -sf -X POST "$API_URL/users?sync=true" \
    -H "Content-Type: application/json" \
    -d "$REGISTER_BODY")
reg_status2=$(echo "$reg_resp2" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$reg_status2" = "registered" ]; then
    # Give Docker a moment to connect the network
    sleep 2
    nginx_connected=$(docker network inspect "$TEST_NETWORK_NAME" \
        --format '{{range $k,$v := .Containers}}{{$v.Name}} {{end}}' 2>/dev/null \
        | grep -c 'provision-nginx' || true)
    print_networks "after re-registration"
    if [ "$nginx_connected" -ge 1 ]; then
        pass "provision-nginx is connected to network $TEST_NETWORK_NAME"
    else
        fail "provision-nginx is NOT connected to network $TEST_NETWORK_NAME"
    fi
else
    fail "Re-registration failed: $reg_resp2"
fi

# ---------------------------------------------------------------------------
# Test 12: User network is removed after de-registration
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 12: User network removed after de-registration ---"
del_resp2=$(curl -sf -X DELETE "$API_URL/users/testuser/services/myapp/0?sync=true")
sleep 3
net_exists=$(docker network ls --format '{{.Name}}' 2>/dev/null \
    | grep -c "^${TEST_NETWORK_NAME}$" || true)
print_networks "after second removal"
if [ "$net_exists" -eq 0 ]; then
    pass "Network $TEST_NETWORK_NAME is removed after de-registration"
else
    fail "Network $TEST_NETWORK_NAME still exists after de-registration"
fi

# ---------------------------------------------------------------------------
# Test 13: POST /users with compose_file_path (plain file — auto-converted)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 13: POST /users with compose_file_path + nginx_conf_file_path, passwd='' ---"
REGISTER_BODY_FC=$(cat <<EOF
{
  "user_name": "fileuser",
  "service_name": "myapp",
  "compose_file_path": "${PROVISION_DIR}/templates/docker-compose.plain.yml",
  "nginx_conf_file_path": "${PROVISION_DIR}/templates/myapp.plain.nginx.conf",
  "label": "0",
  "domain": "localhost",
  "passwd": "",
  "volumes": {
    "html": "${PROVISION_DIR}/user-data/fileuser/html",
    "db":   "${PROVISION_DIR}/user-data/fileuser/db"
  }
}
EOF
)
fc_resp=$(curl -sf -X POST "$API_URL/users?sync=true" \
    -H "Content-Type: application/json" \
    -d "$REGISTER_BODY_FC")
fc_status=$(echo "$fc_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$fc_status" = "registered" ]; then
    pass "POST /users with compose_file_path registered fileuser/myapp/0"
else
    fail "POST /users with compose_file_path failed: $fc_resp"
fi

# passwd="" → htpasswd_path must be null in response
fc_htpasswd=$(echo "$fc_resp" | python3 -c "import sys,json; e=json.load(sys.stdin)['entry']; print(e.get('htpasswd_path') or 'null')" 2>/dev/null || echo "err")
if [ "$fc_htpasswd" = "null" ]; then
    pass "passwd='' → htpasswd_path is null in registry"
else
    fail "Expected htpasswd_path=null for empty passwd, got: $fc_htpasswd"
fi

# No .htpasswd file should exist on disk
FC_HTPASSWD_FILE="${PROVISION_DIR}/generated/myapp.user-fileuser.0.htpasswd"
if [ ! -f "$FC_HTPASSWD_FILE" ]; then
    pass "passwd='' → no .htpasswd file created on disk"
else
    fail "Unexpected .htpasswd file found: $FC_HTPASSWD_FILE"
fi

# Rendered nginx conf must not contain auth_basic directives
FC_NGINX_CONF="${PROVISION_DIR}/generated/myapp.user-fileuser.0.nginx.conf"
if [ -f "$FC_NGINX_CONF" ]; then
    if grep -q "auth_basic" "$FC_NGINX_CONF"; then
        fail "passwd='' → nginx conf should have no auth_basic, but found one"
    else
        pass "passwd='' → nginx conf has no auth_basic directives"
    fi
    # Verify proxy_pass target was rendered with container_prefix (hint-based:
    # "myapp-web" → "{{ container_prefix }}web" → "myapp-user_fileuser-0-web")
    if grep -q "proxy_pass.*myapp-user_fileuser-0-web:80" "$FC_NGINX_CONF"; then
        pass "proxy_pass rendered with correct container_prefix (hint-based)"
    else
        fail "proxy_pass missing rendered container name (hint-based): $(grep proxy_pass "$FC_NGINX_CONF" || echo 'no proxy_pass found')"
    fi
    # The raw hint-prefixed host should NOT appear in the rendered output
    if grep -q "http://myapp-web" "$FC_NGINX_CONF"; then
        fail "proxy_pass still has literal 'myapp-web' (not templatized)"
    else
        pass "proxy_pass does not contain literal 'myapp-web'"
    fi
else
    fail "Nginx conf not found at: $FC_NGINX_CONF"
fi

# Clean up fileuser so it doesn't affect subsequent runs
curl -sf -X DELETE "$API_URL/users/fileuser/services/myapp/0" >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Test 14: POST /users with neither compose source returns 422
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 14: POST /users with no compose source (expect 422) ---"
no_src_http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL/users" \
    -H "Content-Type: application/json" \
    -d '{"user_name":"x","service_name":"y"}')
if [ "$no_src_http" = "422" ]; then
    pass "POST /users with no compose source returns 422"
else
    fail "Expected 422 when no compose source provided, got: $no_src_http"
fi

# ---------------------------------------------------------------------------
# Test 15: POST /users with both compose_template_path and compose_file_path returns 422
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 15: POST /users with both compose sources (expect 422) ---"
both_src_http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL/users" \
    -H "Content-Type: application/json" \
    -d "{\"user_name\":\"x\",\"service_name\":\"y\",\"compose_template_path\":\"a\",\"compose_file_path\":\"b\"}")
if [ "$both_src_http" = "422" ]; then
    pass "POST /users with both compose sources returns 422"
else
    fail "Expected 422 when both compose sources provided, got: $both_src_http"
fi

# ---------------------------------------------------------------------------
# Test 16: Default passwd (123456) → htpasswd generated, nginx conf has auth_basic
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 16: Default passwd → htpasswd generated + nginx conf has auth_basic ---"
mkdir -p "${PROVISION_DIR}/user-data/nginxuser/app" "${PROVISION_DIR}/user-data/nginxuser/db"
REGISTER_BODY_NX=$(cat <<EOF
{
  "user_name": "nginxuser",
  "service_name": "myapp",
  "compose_template_path": "${PROVISION_DIR}/templates/docker-compose.template.yml.j2",
  "nginx_conf_template_path": "${PROVISION_DIR}/templates/myapp.template.nginx.conf.j2",
  "label": "0",
  "domain": "localhost",
  "volumes": {
    "app_data": "${PROVISION_DIR}/user-data/nginxuser/app",
    "db_data":  "${PROVISION_DIR}/user-data/nginxuser/db"
  }
}
EOF
)
nx_resp=$(curl -sf -X POST "$API_URL/users?sync=true" \
    -H "Content-Type: application/json" \
    -d "$REGISTER_BODY_NX")
nx_status=$(echo "$nx_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$nx_status" = "registered" ]; then
    pass "Default passwd registration: nginxuser/myapp/0 registered"
else
    fail "Default passwd registration failed: $nx_resp"
fi

# htpasswd_path must be non-null in response
nx_htpasswd=$(echo "$nx_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['entry'].get('htpasswd_path') or 'null')" 2>/dev/null || echo "null")
if [ "$nx_htpasswd" != "null" ] && [ "$nx_htpasswd" != "" ]; then
    pass "Default passwd → htpasswd_path is set in registry: $nx_htpasswd"
else
    fail "Expected htpasswd_path to be set, got: $nx_htpasswd"
fi

# .htpasswd file must exist on disk
NX_HTPASSWD_FILE="${PROVISION_DIR}/generated/myapp.user-nginxuser.0.htpasswd"
if [ -f "$NX_HTPASSWD_FILE" ]; then
    pass "Default passwd → .htpasswd file created on disk"
    # First line must look like a bcrypt hash (nginxuser:$2...)
    first_line=$(head -1 "$NX_HTPASSWD_FILE")
    if echo "$first_line" | grep -qE "^nginxuser:\\\$2"; then
        pass "Default passwd → .htpasswd contains bcrypt hash for nginxuser"
    else
        fail "Unexpected .htpasswd content: $first_line"
    fi
else
    fail ".htpasswd file not found at: $NX_HTPASSWD_FILE"
fi

# Rendered nginx conf must contain auth_basic directives
NX_NGINX_CONF="${PROVISION_DIR}/generated/myapp.user-nginxuser.0.nginx.conf"
if [ -f "$NX_NGINX_CONF" ]; then
    if grep -q "auth_basic" "$NX_NGINX_CONF" && grep -q "auth_basic_user_file" "$NX_NGINX_CONF"; then
        pass "Default passwd → nginx conf has auth_basic directives"
    else
        fail "Default passwd → nginx conf missing auth_basic (got: $(cat "$NX_NGINX_CONF"))"
    fi
else
    fail "Nginx conf not found at: $NX_NGINX_CONF"
fi

# Clean up
curl -sf -X DELETE "$API_URL/users/nginxuser/services/myapp/0" >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Test 17: project_root bare name resolves to SOURCE_PROJECTS_DIR/{name}
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 17: project_root bare name → SOURCE_PROJECTS_DIR/testpr ---"
# SOURCE_PROJECTS_DIR inside the container is ${PROVISION_DIR}/source_projects (set by docker-compose)
mkdir -p "${PROVISION_DIR}/source_projects/testpr"
cp "$PROVISION_DIR/templates/docker-compose.template.yml.j2" "${PROVISION_DIR}/source_projects/testpr/"
cp "$PROVISION_DIR/templates/myapp.template.nginx.conf.j2"  "${PROVISION_DIR}/source_projects/testpr/" 2>/dev/null || true

REGISTER_BODY_PR=$(cat <<EOF
{
  "user_name": "pruser",
  "service_name": "myapp",
  "project_root": "testpr",
  "compose_template_path": "docker-compose.template.yml.j2",
  "nginx_conf_template_path": "myapp.template.nginx.conf.j2",
  "label": "0",
  "domain": "localhost",
  "passwd": "secret"
}
EOF
)
pr_resp=$(curl -sf -X POST "$API_URL/users?sync=true" \
    -H "Content-Type: application/json" \
    -d "$REGISTER_BODY_PR")
pr_status=$(echo "$pr_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$pr_status" = "registered" ]; then
    pass "Bare project_root 'testpr' resolves to SOURCE_PROJECTS_DIR/testpr: registration succeeded"
else
    fail "Bare project_root registration failed: $pr_resp"
fi

# Compose file should be written inside source_projects/testpr/, not in templates/
pr_compose=$(echo "$pr_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['entry'].get('compose_file_path',''))" 2>/dev/null || echo "")
if echo "$pr_compose" | grep -q "source_projects/testpr"; then
    pass "Generated compose file written inside project_root (source_projects/testpr): $pr_compose"
else
    fail "Expected compose path under source_projects/testpr, got: $pr_compose"
fi

# Verify that a bare name pointing to a non-existent dir returns 404
pr_404=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL/users" \
    -H "Content-Type: application/json" \
    -d '{"user_name":"x","service_name":"y","project_root":"no_such_dir","compose_template_path":"a.yml.j2"}')
if [ "$pr_404" = "404" ]; then
    pass "Bare project_root pointing to non-existent dir returns 404"
else
    fail "Expected 404 for non-existent bare project_root, got: $pr_404"
fi

# Clean up
curl -sf -X DELETE "$API_URL/users/pruser/services/myapp/0" >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Test 18: Start MockProxy and register with build_args pointing to it
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 18: Register with build_args → MockProxy ---"

# Start the MockProxy on a random port — write its URL to a temp file
MOCK_PROXY_PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")
MOCK_PROXY_URL_FILE="${PROVISION_DIR}/generated/.mock_proxy_url"
MOCK_PROXY_PID=""
echo "  Starting MockProxy on port $MOCK_PROXY_PORT ..."
python3 -c "
import sys, threading
sys.path.insert(0, '$SCRIPT_DIR')
from mock_proxy import MockProxy
proxy = MockProxy(port=$MOCK_PROXY_PORT)
proxy.start()
with open('${MOCK_PROXY_URL_FILE}', 'w') as f:
    f.write(proxy.url)
# Block until killed
threading.Event().wait()
" &
MOCK_PROXY_PID=$!
sleep 2  # give the proxy time to start and write URL

# Read the MockProxy URL from the temp file
if [ -f "$MOCK_PROXY_URL_FILE" ]; then
    MOCK_PROXY_URL=$(cat "$MOCK_PROXY_URL_FILE")
    echo "  MockProxy running at $MOCK_PROXY_URL (PID $MOCK_PROXY_PID)"
fi

if [ -z "${MOCK_PROXY_URL:-}" ] || ! kill -0 "$MOCK_PROXY_PID" 2>/dev/null; then
    MOCK_PROXY_URL="http://127.0.0.1:${MOCK_PROXY_PORT}"
    fail "MockProxy failed to start or write URL file"
fi

# Register with build_args pointing to MockProxy
mkdir -p "${PROVISION_DIR}/user-data/mockpx/app" "${PROVISION_DIR}/user-data/mockpx/db"
REGISTER_BODY_MPX=$(cat <<EOF
{
  "user_name": "mockpx",
  "service_name": "myapp",
  "compose_template_path": "${PROVISION_DIR}/templates/docker-compose.template.yml.j2",
  "label": "0",
  "domain": "localhost",
  "passwd": "",
  "volumes": {
    "app_data": "${PROVISION_DIR}/user-data/mockpx/app",
    "db_data":  "${PROVISION_DIR}/user-data/mockpx/db"
  },
  "build_args": {
    "HTTP_PROXY": "${MOCK_PROXY_URL}",
    "HTTPS_PROXY": "${MOCK_PROXY_URL}"
  }
}
EOF
)
mpx_resp=$(curl -sf -X POST "$API_URL/users?sync=true" \
    -H "Content-Type: application/json" \
    -d "$REGISTER_BODY_MPX")
mpx_status=$(echo "$mpx_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$mpx_status" = "registered" ]; then
    pass "Register with MockProxy build_args succeeded"
else
    fail "Register with MockProxy build_args failed: $mpx_resp"
fi

# Verify build_args stored in response
mpx_ba=$(echo "$mpx_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['entry'].get('build_args','{}'))" 2>/dev/null || echo "{}")
if echo "$mpx_ba" | grep -q "$MOCK_PROXY_URL"; then
    pass "build_args contain MockProxy URL in registry response"
else
    fail "build_args missing MockProxy URL: $mpx_ba"
fi

# Verify proxy URL appears in docker_ops.log
DOCKER_OPS_LOG="${PROVISION_DIR}/generated/docker_ops.log"
if [ -f "$DOCKER_OPS_LOG" ]; then
    if grep -q "$MOCK_PROXY_URL" "$DOCKER_OPS_LOG"; then
        pass "docker_ops.log contains MockProxy URL as --build-arg"
    else
        fail "docker_ops.log missing MockProxy URL (checked: $MOCK_PROXY_URL)"
    fi
else
    fail "docker_ops.log not found at $DOCKER_OPS_LOG"
fi

# ---------------------------------------------------------------------------
# Test 19: Rebuild with override build_args → MockProxy
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 19: Rebuild with override build_args → MockProxy ---"
OVERRIDE_PROXY="http://127.0.0.1:${MOCK_PROXY_PORT}"
rebuild_mpx_resp=$(curl -sf -X POST "$API_URL/users/mockpx/services/myapp/0/rebuild?sync=true" \
    -H "Content-Type: application/json" \
    -d "{\"no_cache\": true, \"build_args\": {\"HTTP_PROXY\": \"${OVERRIDE_PROXY}\", \"NO_PROXY\": \"localhost\"}}")
rebuild_mpx_status=$(echo "$rebuild_mpx_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$rebuild_mpx_status" = "rebuilt" ]; then
    pass "Rebuild with MockProxy override build_args returned status=rebuilt"
else
    fail "Rebuild with MockProxy override build_args failed: $rebuild_mpx_resp"
fi

# Check the override URL appeared in docker_ops.log AFTER rebuild
if [ -f "$DOCKER_OPS_LOG" ]; then
    if grep -q "NO_PROXY=localhost" "$DOCKER_OPS_LOG"; then
        pass "docker_ops.log contains NO_PROXY from rebuild override"
    else
        fail "docker_ops.log missing NO_PROXY=localhost from rebuild"
    fi
else
    fail "docker_ops.log not found"
fi

# Stop MockProxy
if [ -n "$MOCK_PROXY_PID" ] && kill -0 "$MOCK_PROXY_PID" 2>/dev/null; then
    kill "$MOCK_PROXY_PID" 2>/dev/null || true
    wait "$MOCK_PROXY_PID" 2>/dev/null || true
    echo "  MockProxy stopped"
fi
rm -f "$MOCK_PROXY_URL_FILE"

# Clean up mockpx
curl -sf -X DELETE "$API_URL/users/mockpx/services/myapp/0" >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# Test 20: Async register — returns task_id, poll until complete
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 20: Async register → task_id → poll → complete ---"
mkdir -p "${PROVISION_DIR}/user-data/asynctest/app" "${PROVISION_DIR}/user-data/asynctest/db"
ASYNC_BODY=$(cat <<EOF
{
  "user_name": "asynctest",
  "service_name": "myapp",
  "compose_template_path": "${PROVISION_DIR}/templates/docker-compose.template.yml.j2",
  "label": "0",
  "domain": "localhost",
  "passwd": "",
  "volumes": {
    "app_data": "${PROVISION_DIR}/user-data/asynctest/app",
    "db_data":  "${PROVISION_DIR}/user-data/asynctest/db"
  }
}
EOF
)
async_resp=$(curl -sf -X POST "$API_URL/users" \
    -H "Content-Type: application/json" \
    -d "$ASYNC_BODY")
async_task_id=$(echo "$async_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])" 2>/dev/null || echo "")
async_type=$(echo "$async_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['type'])" 2>/dev/null || echo "")

if [ -n "$async_task_id" ] && [ "$async_type" = "register" ]; then
    pass "Async register returned task_id=$async_task_id"
else
    fail "Async register did not return task_id: $async_resp"
fi

# Poll until completed
async_done=0
for i in $(seq 1 30); do
    task_status=$(curl -sf "$API_URL/tasks/$async_task_id")
    ts=$(echo "$task_status" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
    echo "  [${i}s] task $async_task_id status=$ts"
    if [ "$ts" = "completed" ]; then
        async_done=1
        echo "  Task result: $(echo "$task_status" | python3 -c "import sys,json; r=json.load(sys.stdin).get('result',''); print(str(r)[:100])" 2>/dev/null || echo "")"
        pass "Async task $async_task_id completed after ${i}s"
        break
    elif [ "$ts" = "failed" ]; then
        err=$(echo "$task_status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null || echo "")
        fail "Async task $async_task_id failed: $err"
        break
    fi
    sleep 1
done
if [ "$async_done" -eq 0 ]; then
    fail "Async task $async_task_id did not complete within 30s (last status=$ts)"
fi

# ---------------------------------------------------------------------------
# Test 21: GET /tasks — list all tasks (includes the completed one)
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 21: GET /tasks lists all tasks ---"
all_tasks=$(curl -sf "$API_URL/tasks")
task_count=$(echo "$all_tasks" | python3 -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "0")
# Print a summary table of all tasks
echo "  Task pool summary:"
echo "$all_tasks" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f'  {\"TASK ID\":<14} {\"TYPE\":<10} {\"STATUS\":<12} {\"RESULT/ERROR\"}')
print(f'  {\"-\"*14} {\"-\"*10} {\"-\"*12} {\"-\"*20}')
for t in data['tasks']:
    extra = str(t.get('result','') or t.get('error',''))[:30]
    print(f'  {t[\"task_id\"]:<14} {t[\"type\"]:<10} {t[\"status\"]:<12} {extra}')
" 2>/dev/null || true
if [ "$task_count" -ge 1 ]; then
    pass "GET /tasks returned $task_count task(s)"
else
    fail "GET /tasks returned 0 tasks, expected >= 1"
fi

# Verify our task is in the list
in_list=$(echo "$all_tasks" | python3 -c "
import sys, json
data = json.load(sys.stdin)
found = any(t['task_id'] == '$async_task_id' for t in data['tasks'])
print('yes' if found else 'no')
" 2>/dev/null || echo "no")
if [ "$in_list" = "yes" ]; then
    pass "GET /tasks includes $async_task_id"
else
    fail "GET /tasks missing $async_task_id"
fi

# ---------------------------------------------------------------------------
# Test 22: DELETE /tasks/{task_id} — cancel a pending task
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 22: Cancel a pending task ---"
# Submit a task and immediately cancel it
cancel_body='{"user_name":"cancelme","service_name":"myapp","compose_template_path":"'"${PROVISION_DIR}/templates/docker-compose.template.yml.j2"'","label":"0","volumes":{"app_data":"'"${PROVISION_DIR}/user-data/asynctest/app"'","db_data":"'"${PROVISION_DIR}/user-data/asynctest/db"'"}}'
cancel_resp=$(curl -sf -X POST "$API_URL/users" \
    -H "Content-Type: application/json" \
    -d "$cancel_body")
cancel_tid=$(echo "$cancel_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])" 2>/dev/null || echo "")

if [ -n "$cancel_tid" ]; then
    cancel_result=$(curl -sf -X DELETE "$API_URL/tasks/$cancel_tid")
    cancel_status=$(echo "$cancel_result" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
    if [ "$cancel_status" = "cancelled" ]; then
        pass "Cancel task $cancel_tid returned status=cancelled"
    else
        fail "Cancel task returned unexpected: $cancel_result"
    fi
else
    fail "Could not submit task for cancellation test"
fi

# ---------------------------------------------------------------------------
# Test 23: GET /tasks/{task_id} — 404 for nonexistent
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 23: GET /tasks/{nonexistent} returns 404 ---"
t404_http=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/tasks/nonexistent999")
if [ "$t404_http" = "404" ]; then
    pass "GET /tasks/nonexistent returns 404"
else
    fail "Expected 404 for nonexistent task, got: $t404_http"
fi

# ---------------------------------------------------------------------------
# Test 24: proxy_pass compose service name detection
#          When nginx.conf uses a compose service name (not hint-prefixed)
#          in proxy_pass, the converter detects it and rewrites to
#          {{ container_prefix }}<service_name>.
# ---------------------------------------------------------------------------
echo ""
echo "--- Test 24: proxy_pass compose service name detection ---"

# Write a plain docker-compose.yml with services "web" and "db"
COMPOSE_SVC_TEST_DIR="${PROVISION_DIR}/source_projects/svcname_test"
mkdir -p "$COMPOSE_SVC_TEST_DIR"
cat > "${COMPOSE_SVC_TEST_DIR}/docker-compose.yml" <<'COMPOSE_EOF'
services:
  web:
    image: nginx:alpine
    container_name: web
    expose:
      - "80"
    volumes:
      - html:/usr/share/nginx/html:ro
    networks:
      - svc-net
  db:
    image: postgres:16-alpine
    container_name: db
    environment:
      - POSTGRES_DB=svctest
    volumes:
      - db:/var/lib/postgresql/data
    networks:
      - svc-net

volumes:
  html:
  db:

networks:
  svc-net:
COMPOSE_EOF

# Write a plain nginx.conf that uses the compose SERVICE NAME "web"
# (NOT "myapp-web" — deliberately not prefixed with the provision service_name_hint)
cat > "${COMPOSE_SVC_TEST_DIR}/nginx.conf" <<'NGINX_EOF'
server {
    listen 80;
    server_name svcname.example.com;

    location / {
        proxy_pass http://web:80;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX_EOF

mkdir -p "${PROVISION_DIR}/user-data/svcuser/html" "${PROVISION_DIR}/user-data/svcuser/db"

SVC_REG_BODY=$(cat <<EOF
{
  "user_name": "svcuser",
  "service_name": "svcname_test",
  "project_root": "svcname_test",
  "compose_file_path": "docker-compose.yml",
  "nginx_conf_file_path": "nginx.conf",
  "label": "0",
  "domain": "localhost",
  "passwd": "",
  "volumes": {
    "html": "${PROVISION_DIR}/user-data/svcuser/html",
    "db":   "${PROVISION_DIR}/user-data/svcuser/db"
  }
}
EOF
)
svc_resp=$(curl -sf -X POST "$API_URL/users?sync=true" \
    -H "Content-Type: application/json" \
    -d "$SVC_REG_BODY")
svc_status=$(echo "$svc_resp" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "")
if [ "$svc_status" = "registered" ]; then
    pass "Compose-service-name nginx: svcuser/svcname_test/0 registered"
else
    fail "Compose-service-name nginx registration failed: $svc_resp"
fi

# --- Verify the generated .j2 template (intermediate file) ---
SVC_J2="${COMPOSE_SVC_TEST_DIR}/nginx.conf.j2"
if [ -f "$SVC_J2" ]; then
    # The template must have {{ container_prefix }}web (compose service name "web" detected)
    if grep -q "{{ container_prefix }}web" "$SVC_J2"; then
        pass ".j2 template has {{ container_prefix }}web (compose service name detected)"
    else
        fail ".j2 template missing {{ container_prefix }}web: $(grep proxy_pass "$SVC_J2" || echo 'no proxy_pass')"
    fi
    # The original literal "http://web:" should NOT remain
    if grep -q "http://web:" "$SVC_J2"; then
        fail ".j2 template still has literal 'http://web:' (not replaced)"
    else
        pass ".j2 template does not contain literal 'http://web:'"
    fi
else
    fail ".j2 template not found at: $SVC_J2"
fi

# --- Verify the rendered nginx conf ---
SVC_NGINX="${PROVISION_DIR}/generated/svcname_test.user-svcuser.0.nginx.conf"
if [ -f "$SVC_NGINX" ]; then
    # The rendered conf must have the full container name: svcname_test-user_svcuser-0-web
    if grep -q "proxy_pass.*svcname_test-user_svcuser-0-web:80" "$SVC_NGINX"; then
        pass "Rendered nginx conf has correct container name in proxy_pass"
    else
        fail "Rendered nginx conf missing container name: $(grep proxy_pass "$SVC_NGINX" || echo 'no proxy_pass')"
    fi
    # The raw template variable {{ container_prefix }} must NOT appear
    if grep -q "{{ container_prefix }}" "$SVC_NGINX"; then
        fail "Rendered nginx conf has unrendered {{ container_prefix }}"
    else
        pass "Rendered nginx conf has no unrendered template variables"
    fi
else
    fail "Rendered nginx conf not found at: $SVC_NGINX"
fi

# --- Also verify the .j2 template for the compose file was created ---
SVC_COMPOSE_J2="${COMPOSE_SVC_TEST_DIR}/docker-compose.yml.j2"
if [ -f "$SVC_COMPOSE_J2" ]; then
    pass "Compose .j2 template created: docker-compose.yml.j2"
else
    fail "Compose .j2 template not found at: $SVC_COMPOSE_J2"
fi

# --- Verify compose service names were correctly extracted ---
# (The compose has "web" and "db" — both should appear in the .j2 template
#  with {{ container_prefix }} prefix in container_name)
if [ -f "$SVC_COMPOSE_J2" ]; then
    if grep -q "{{ container_prefix }}web" "$SVC_COMPOSE_J2" && \
       grep -q "{{ container_prefix }}db" "$SVC_COMPOSE_J2"; then
        pass "Compose .j2 template has container_prefix for web and db services"
    else
        fail "Compose .j2 template missing container_prefix for services"
    fi
fi

# Clean up svcuser
curl -sf -X DELETE "$API_URL/users/svcuser/services/svcname_test/0" >/dev/null 2>&1 || true
# Clean up the temp source project
rm -rf "$COMPOSE_SVC_TEST_DIR"

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
echo ""
echo "================================"
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "================================"

[ "$FAIL" -eq 0 ] || exit 1
