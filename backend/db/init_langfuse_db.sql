-- Bootstrap a separate logical database for Langfuse on the same Postgres instance.
-- Runs once on first container start (docker-entrypoint-initdb.d).
SELECT 'CREATE DATABASE langfuse OWNER ffa'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec
