-- Read-only analytics role. Applied by opsquery.db.migrate on every run.
-- __DATABASE__ is substituted with the target database name by the runner.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_readonly') THEN
        CREATE ROLE ops_readonly LOGIN PASSWORD 'ops_readonly_dev';
    END IF;
END
$$;

ALTER ROLE ops_readonly SET statement_timeout = '5s';
ALTER ROLE ops_readonly SET default_transaction_read_only = on;
ALTER ROLE ops_readonly SET idle_in_transaction_session_timeout = '10s';

GRANT CONNECT ON DATABASE "__DATABASE__" TO ops_readonly;

REVOKE ALL ON SCHEMA public FROM ops_readonly;
GRANT USAGE ON SCHEMA public TO ops_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO ops_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO ops_readonly;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO ops_readonly;

-- The migration bookkeeping schema is explicitly out of reach.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'ops_migrations') THEN
        EXECUTE 'REVOKE ALL ON SCHEMA ops_migrations FROM ops_readonly';
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA ops_migrations FROM ops_readonly';
    END IF;
END
$$;
