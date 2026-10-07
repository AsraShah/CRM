-- Create the three database roles the design depends on (SVX-TECH-001 §5.3).
--
-- Run once against each database (svx and svx_test) as a superuser.
-- The Docker path uses infra/postgres/00-roles.sh instead; this file is the
-- equivalent for a local or managed PostgreSQL where that init hook does not
-- run.
--
--   svx_owner      owns the tables. Used only by `migrate --database=owner`.
--   svx_app        the served application. NOT the owner, no BYPASSRLS, so
--                  FORCE ROW LEVEL SECURITY actually constrains it.
--   svx_scheduler  queue metadata only: jobs and outbox events.
--
-- Keeping these apart is the whole point. If the application connects as the
-- owner, row-level security stops being a boundary it cannot cross, and the
-- isolation tests pass without testing anything.
--
-- Passwords here are development defaults. Change them anywhere real.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svx_owner') THEN
        CREATE ROLE svx_owner LOGIN PASSWORD 'svx_owner_pw' NOBYPASSRLS CREATEDB;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svx_app') THEN
        CREATE ROLE svx_app LOGIN PASSWORD 'svx_app_pw' NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svx_scheduler') THEN
        CREATE ROLE svx_scheduler LOGIN PASSWORD 'svx_sched_pw' NOBYPASSRLS;
    END IF;
END $$;

-- The owner creates and owns everything in `public`.
ALTER SCHEMA public OWNER TO svx_owner;
GRANT ALL ON SCHEMA public TO svx_owner;

-- Nobody gets table rights by default; the RLS migrations grant exactly what
-- each role needs, table by table.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO svx_app, svx_scheduler;

-- Tables the owner creates later must be reachable by the app role without
-- another manual grant, but still only with the privileges the migration sets.
ALTER DEFAULT PRIVILEGES FOR ROLE svx_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO svx_app;
ALTER DEFAULT PRIVILEGES FOR ROLE svx_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO svx_app;

-- Neither runtime role may create objects.
REVOKE CREATE ON SCHEMA public FROM svx_app, svx_scheduler;
