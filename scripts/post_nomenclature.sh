#!/usr/bin/env bash
# POST /api/1/nomenclature — curl (Linux/macOS/Git Bash).
# Токен: POST /api/1/access_token
#
#   export IIKO_API_LOGIN=...
#   export IIKO_ORGANIZATION_ID=...
#   ./scripts/post_nomenclature.sh
#   ./scripts/post_nomenclature.sh --out nomenclature.json
set -euo pipefail

BASE_URL="${IIKO_BASE_URL:-https://api-ru.iiko.services}"
OUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT="${2:-}"; shift 2 ;;
    --login) IIKO_API_LOGIN="${2:-}"; shift 2 ;;
    --org) IIKO_ORGANIZATION_ID="${2:-}"; shift 2 ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 1 ;;
  esac
done

: "${IIKO_API_LOGIN:?Задайте IIKO_API_LOGIN}"
: "${IIKO_ORGANIZATION_ID:?Задайте IIKO_ORGANIZATION_ID}"

BASE_URL="${BASE_URL%/}"

TOKEN_JSON="$(curl -sS -X POST "${BASE_URL}/api/1/access_token" \
  -H "Content-Type: application/json" \
  -d "{\"apiLogin\":\"${IIKO_API_LOGIN}\"}")"

TOKEN="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('token') or '')" "$TOKEN_JSON")"
if [[ -z "$TOKEN" ]]; then
  echo "Нет token в ответе access_token: $TOKEN_JSON" >&2
  exit 1
fi

echo "POST ${BASE_URL}/api/1/nomenclature (organizationId=${IIKO_ORGANIZATION_ID})"

if [[ -n "$OUT" ]]; then
  curl -sS -X POST "${BASE_URL}/api/1/nomenclature" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -d "{\"organizationId\":\"${IIKO_ORGANIZATION_ID}\"}" \
    --max-time 120 \
    -o "$OUT"
  echo "Сохранено: $OUT"
  python3 - <<'PY' "$OUT"
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    d = json.load(f)
g = d.get("groups") or []
p = d.get("products") or []
print(f"groups(top-level count): {len(g)}")
print(f"products(count): {len(p)}")
PY
else
  BODY="$(curl -sS -X POST "${BASE_URL}/api/1/nomenclature" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${TOKEN}" \
    -d "{\"organizationId\":\"${IIKO_ORGANIZATION_ID}\"}" \
    --max-time 120)"
  printf '%s' "${BODY}" | python3 -c "import json,sys;d=json.load(sys.stdin);g=d.get('groups')or[];p=d.get('products')or[];print('groups(top-level count):',len(g));print('products(count):',len(p))"
fi
