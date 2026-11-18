-- Database roles for the application. Run as a superuser, connected to the
-- application database. Idempotent: every run converges both roles to the state
-- described here, so it is also how passwords are rotated.
--
--   helpdesk_owner  owns the database and every table; runs the migrations.
--   helpdesk_app    used by Django at runtime; subject to row-level security.
--
-- Roles belong to the whole cluster, not to one database, and creating them
-- needs CREATEROLE. That is why this lives here and not in a migration.
--
-- Passwords come from the HELPDESK_OWNER_PASSWORD and HELPDESK_APP_PASSWORD
-- environment variables (\getenv needs psql 15 or newer).

\set ON_ERROR_STOP on

\getenv owner_password HELPDESK_OWNER_PASSWORD
\getenv app_password HELPDESK_APP_PASSWORD

-- \getenv leaves the variable unset when the environment variable is missing,
-- and an unset variable would reach the SQL below as literal text. Treat it as
-- empty so the check that follows catches both cases.
\if :{?owner_password}
\else
\set owner_password ''
\endif
\if :{?app_password}
\else
\set app_password ''
\endif

SELECT :'owner_password' = '' OR :'app_password' = '' AS missing_password \gset
\if :missing_password
DO $$
BEGIN
    RAISE EXCEPTION 'Set HELPDESK_OWNER_PASSWORD and HELPDESK_APP_PASSWORD';
END
$$;
\endif

-- All or nothing: a failure leaves the cluster as it was.
BEGIN;

-- CREATE ROLE has no IF NOT EXISTS. \gexec runs each row returned by the query
-- as a statement, so the role is created only when the query returns a row.
SELECT 'CREATE ROLE helpdesk_owner'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'helpdesk_owner')
\gexec

SELECT 'CREATE ROLE helpdesk_app'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'helpdesk_app')
\gexec

-- Always applied, with every attribute spelled out (even the defaults), so a
-- role changed by hand is brought back to this state. :'name' inserts the psql
-- variable as a properly quoted SQL literal.
--
-- CREATEDB: pytest-django creates and drops the test database as this role.
ALTER ROLE helpdesk_owner WITH
    LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'owner_password';

-- NOBYPASSRLS is the point of this role: row-level security always applies.
ALTER ROLE helpdesk_app WITH
    LOGIN NOCREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'app_password';

-- Lets the isolation tests run SET LOCAL ROLE helpdesk_app inside the test
-- transaction. INHERIT FALSE: the owner does not silently gain the app's
-- privileges; it only gets them after an explicit SET ROLE.
GRANT helpdesk_app TO helpdesk_owner WITH INHERIT FALSE, SET TRUE;

-- The owner of the database has CREATE on it, which is enough to install
-- trusted extensions such as btree_gist. Since PostgreSQL 15 the public schema
-- belongs to pg_database_owner, so this also makes helpdesk_owner its owner.
-- :"DBNAME" is the current database, quoted as an identifier.
ALTER DATABASE :"DBNAME" OWNER TO helpdesk_owner;

COMMIT;
