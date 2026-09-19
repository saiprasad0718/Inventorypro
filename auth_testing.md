# Auth Testing Playbook — InventoryPro.in

Auth: JWT bearer tokens (12h TTL), bcrypt password hashes, MongoDB users collection.
Roles: owner (full access), manager (all except Settings), chef (Dashboard, Production, Consumption, Wastage, Reconciliation read).

## Step 1: MongoDB verification
```
mongosh
use <database_name>
db.users.find({role: "owner"}, {password_hash: 1, email: 1})
```
Verify: bcrypt hash starts with `$2b$`, unique index on users.email, index on login_attempts.identifier.

## Step 2: API testing
```
TOKEN=$(curl -s -X POST $API/api/auth/login -H "Content-Type: application/json" -d '{"email":"<owner email>","password":"<password>"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s $API/api/auth/me -H "Authorization: Bearer $TOKEN"
curl -s $API/api/dashboard -H "Authorization: Bearer $TOKEN"
```
Login returns {token, user}. /me returns the same user. /dashboard returns today's aggregates.

## Brute force
5 failed logins for an IP+email locks that combination for 15 minutes (login_attempts collection).

## Credentials
See /app/memory/test_credentials.md.
