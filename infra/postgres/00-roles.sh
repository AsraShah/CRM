#!/bin/sh
# Create the three database roles the design depends on (SVX-TECH-001 §5.3).
#
# Runs once, when PostgreSQL initialises an empty data directory.
#
#   svx_owner      owns the tables. Used only by `migrate --database=owner`.
#   svx_app        the served application. NOT the owner, no BYPASSRLS, so
#                  FORCE ROW LEVEL SECURITY actually constrains it.
#   svx_scheduler  queue metadata only: jobs and outbox events. It can see that
#                  work is due; it cannot read a contact or a deal.
#
# Keeping these apart is the whole point. If the application connects as the
# owner, row-level security stops being a boundary the application cannot
# cross, and the isolation tests pass without testing anything.

set -eu

: "${POSTGRES_DB:?}"
: "${SVX_OWNER_PASSWORD:?set SVX_OWNER_PASSWORD in the environment}"
: "${SVX_APP_PASSWORD:?set SVX_APP_PASSWORD in the environment}"
: "${SVX_SCHEDULER_PASSWORD:?set SVX_SCHEDULER_PASSWORD in the environment}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v owner_password="'${SVX_OWNER_PASSWORD}'" \
     -v app_password="'${SVX_APP_PASSWORD}'" \
     -v scheduler_password="'${SVX_SCHEDULER_PASSWORD}'" <<'SQL'

CREATE ROLE svx_owner     LOGIN PASSWORD :owner_password     NOBYPASSRLS;
CREATE ROLE svx_app       LOGIN PASSWORD :app_password       NOBYPASSRLS;
CREATE ROLE svx_scheduler LOGIN PASSWORD :scheduler_password NOBYPASSRLS;

-- The owner creates and owns everything in `public`.
ALTER SCHEMA public OWNER TO svx_owner;
GRANT ALL ON SCHEMA public TO svx_owner;

-- Nobody gets table rights by default; the RLS migration grants exactly what
-- each role needs, table by table.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO svx_app, svx_scheduler;

-- Tables created later by the owner must be reachable by the app role without
-- another manual grant, but still only with the privileges the migration sets.
ALTER DEFAULT PRIVILEGES FOR ROLE svx_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO svx_app;
ALTER DEFAULT PRIVILEGES FOR ROLE svx_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO svx_app;

-- Neither runtime role may create objects.
REVOKE CREATE ON SCHEMA public FROM svx_app, svx_scheduler;

SQL

echo "Created svx_owner, svx_app and svx_scheduler."
