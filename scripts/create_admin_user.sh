#!/usr/bin/env bash
# Create (or reset the password of) an admin-dashboard account.
# Run:  bash scripts/create_admin_user.sh
# The password is read from the terminal and never appears in shell history or process lists.
set -euo pipefail
read -rp "Admin username: " U
read -rp "Email (optional): " E
read -rsp "Password (min 10 chars): " P; echo
read -rsp "Repeat password: " P2; echo
[[ "$P" == "$P2" ]] || { echo "Passwords do not match."; exit 1; }
[[ ${#P} -ge 10 ]] || { echo "Password must be at least 10 characters."; exit 1; }
printf '%s\n%s\n%s\n' "$U" "$E" "$P" | docker exec -i admin-dashboard python3 -c '
import os, sys, bcrypt, psycopg2
u, e, p = [l.rstrip("\n") for l in sys.stdin.readlines()[:3]]
h = bcrypt.hashpw(p.encode(), bcrypt.gensalt(12)).decode()
c = psycopg2.connect(host=os.environ["POSTGRES_HOST"], port=os.environ.get("POSTGRES_PORT","5432"),
                     user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"], dbname=os.environ["POSTGRES_DB"])
cur = c.cursor()
cur.execute("""INSERT INTO admin_users (username, email, password_hash) VALUES (%s, %s, %s)
               ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash, is_active = TRUE""", (u, e or None, h))
c.commit(); print("Admin account ready:", u)
'
echo "Sign in at https://compute.explorug.online/gpu-polling/admin/"
