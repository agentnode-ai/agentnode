#!/bin/bash
TOKEN=$(curl -s -X POST http://localhost:8001/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@agentnode.net","password":"testagentnode123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

for slug in api-connector-pack ai-image-generator-pack semantic-search-pack; do
  echo "Re-verifying $slug..."
  curl -s -X POST "http://localhost:8001/v1/packages/$slug/request-reverify" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json"
  echo
done
