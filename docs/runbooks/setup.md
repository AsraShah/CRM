# Developer setup

These steps were **executed on 2026-09-29** on Windows 11 without administrator
rights. Every command below ran; the versions are what actually resolved. See
[`docs/dependency-baseline.md`](../dependency-baseline.md) for the resolved
versions and the five defects that first running the code exposed.

Steps 1–5 are one-off.

---

## 1. Install the toolchain (no administrator rights needed)

Everything installs under `%LOCALAPPDATA%`. Nothing touches Program Files, and
no Windows service is registered.

```powershell
# uv — Python package and version manager
$env:UV_INSTALL_DIR="$env:LOCALAPPDATA\uv\bin"
irm https://astral.sh/uv/install.ps1 | iex
$env:Path = "$env:LOCALAPPDATA\uv\bin;$env:Path"

# Python 3.13, installed by uv
uv python install 3.13
```

Node 24 and pnpm 10:

```powershell
# Download the Node zip rather than the installer, which wants admin.
$v = "v24.21.0"
Invoke-WebRequest "https://nodejs.org/dist/$v/node-$v-win-x64.zip" -OutFile "$env:TEMP\node.zip"
Expand-Archive "$env:TEMP\node.zip" -DestinationPath "$env:TEMP\nodex" -Force
Move-Item "$env:TEMP\nodex\node-$v-win-x64" "$env:LOCALAPPDATA\nodejs"
$env:Path = "$env:LOCALAPPDATA\nodejs;$env:Path"
npm install -g pnpm@10
```

PostgreSQL 17 — the **binaries zip**, not the installer:

```powershell
$url = "https://get.enterprisedb.com/postgresql/postgresql-17.6-1-windows-x64-binaries.zip"
Invoke-WebRequest $url -OutFile "$env:TEMP\pg17.zip"       # ~315 MB
Expand-Archive "$env:TEMP\pg17.zip" -DestinationPath "$env:TEMP\pgtmp" -Force
Move-Item "$env:TEMP\pgtmp\pgsql" "$env:LOCALAPPDATA\pgsql"
```

> **`python` opens the Microsoft Store?** Windows is intercepting the command
> with an App Execution Alias. Turn it off under *Settings → Apps → Advanced app
> settings → App execution aliases*. `uv run python` works regardless.

To make these permanent, add `%LOCALAPPDATA%\uv\bin`, `%LOCALAPPDATA%\nodejs`
and `%LOCALAPPDATA%\pgsql\bin` to your user `PATH`.

---

## 2. Start PostgreSQL

```powershell
$pg   = "$env:LOCALAPPDATA\pgsql\bin"
$data = "$env:LOCALAPPDATA\pgdata"

"postgres" | Set-Content "$env:TEMP\pgpw.txt" -NoNewline
& "$pg\initdb.exe" -D $data -U postgres --pwfile="$env:TEMP\pgpw.txt" --encoding=UTF8 --locale=C
Remove-Item "$env:TEMP\pgpw.txt"

& "$pg\pg_ctl.exe" -D $data -l "$data\server.log" -o "-p 5432" start
& "$pg\pg_isready.exe" -p 5432        # expect: accepting connections
```

Starting it again after a reboot is just the `pg_ctl ... start` line.

---

## 3. Create the databases and the three roles

The role separation is **not optional** — it is what makes row-level security
mean anything (ADR007). If the application connects as the owner, every
isolation test passes without testing anything.

```powershell
$psql = "$env:LOCALAPPDATA\pgsql\bin\psql.exe"; $env:PGPASSWORD = "postgres"

& $psql -U postgres -h 127.0.0.1 -d postgres -c "CREATE DATABASE svx"
& $psql -U postgres -h 127.0.0.1 -d postgres -c "CREATE DATABASE svx_test"

# Run against BOTH databases.
& $psql -U postgres -h 127.0.0.1 -d svx      -v ON_ERROR_STOP=1 -f infra\postgres\roles.sql
& $psql -U postgres -h 127.0.0.1 -d svx_test -v ON_ERROR_STOP=1 -f infra\postgres\roles.sql
```

Confirm none of the three can bypass the policies — all three must read `f|f`:

```powershell
& $psql -U postgres -h 127.0.0.1 -d postgres -tAc `
  "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles WHERE rolname LIKE 'svx%' ORDER BY 1"
```

Passwords in `roles.sql` are development defaults. Change them anywhere real.

---

## 4. Configure the environment

```powershell
Copy-Item .env.example .env
```

Generate the two secrets and paste them in:

```powershell
uv run --directory apps\api python -c "import secrets; print(secrets.token_urlsafe(64))"
uv run --directory apps\api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`manage.py` and `wsgi.py` load `.env` through `config/envfile.py`. An exported
variable always wins over the file, so `FOO=bar uv run python manage.py ...`
still overrides.

---

## 5. Install dependencies and migrate

```powershell
cd apps\api
uv sync
```

Migrations **are committed**. Apply them as the *owner* role:

```powershell
uv run python manage.py migrate --database=owner
```

`common/0003_row_level_security.py` runs last. It applies the policies, the
per-role grants, and a verification block that **refuses to apply** if any
tenant table lacks forced row-level security or has gone missing.

> Regenerating from scratch? The hand-written RLS migration depends on the
> final migration of every app by exact name. Move it aside first, run
> `makemigrations`, then put it back and fix its `dependencies` list — it will
> fail loudly rather than silently skip.

---

## 6. Create the first workspace

Access is invitation-only, so this is the only way in:

```powershell
$env:BOOTSTRAP_PASSWORD = "choose-a-long-password"
uv run python manage.py bootstrap_workspace `
    --name "ScaleVexo" --slug scalevexo `
    --owner-email you@scalevexo.test --owner-name "Your Name"
Remove-Item Env:\BOOTSTRAP_PASSWORD
```

This also marks the owner's email verified. Without that they could never sign
in: verification is mandatory and there is nobody to send them a link.

Install the rule catalogue. All seven install **disabled** — review each
simulation before switching one on:

```powershell
uv run python manage.py seed_rule_catalogue scalevexo
```

---

## 7. Run it

Three terminals:

```powershell
# API
cd apps\api; uv run python manage.py runserver 127.0.0.1:8000

# Worker: one tick per minute
cd apps\api
while ($true) { uv run python manage.py run_scheduler_tick; Start-Sleep 60 }

# Interface
cd apps\web; pnpm install; pnpm dev
```

Open <http://localhost:5173>. Vite proxies `/api` and `/accounts` to Django, so
cookies and CSRF behave exactly as they will behind Caddy in production.

---

## 8. Verify

```powershell
cd apps\api
uv run pytest                      # 181 passed
uv run pytest -m rls               # tenant isolation only
uv run ruff check . ; uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py spectacular --file ..\..\docs\api\openapi.json `
    --format openapi-json --validate --fail-on-warn

cd ..\web
pnpm typecheck ; pnpm test ; pnpm build
```

`tests/test_tenant_isolation.py` **fails deliberately** if the test role is a
superuser or holds `BYPASSRLS`. That is not a misconfiguration to work around:
a passing isolation suite that cannot detect a leak is worse than a failing one.

### End-to-end smoke journey

With the server running, this walks lead → deal → conversion → delivery →
ticket → reporting against real HTTP, real sessions and a real database:

```powershell
cd apps\api
$env:SMOKE_PASSWORD = "the-owner-password-from-step-6"
uv run python smoke_journey.py     # 26 passed
Remove-Item Env:\SMOKE_PASSWORD
```

It is not part of the pytest suite, and that is the point: it exercises the
login view, session cookies and CSRF, which `force_login()` bypasses. Three of
the five defects in the dependency baseline were found here and nowhere else.

The journey writes to a persistent database, so each run creates its own
contact. Re-running accumulates data; that is expected.

---

## Shutting down

```powershell
& "$env:LOCALAPPDATA\pgsql\bin\pg_ctl.exe" -D "$env:LOCALAPPDATA\pgdata" stop
```
