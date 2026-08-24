#!/bin/sh
set -eu

: "${BI_READONLY_PASSWORD:?BI_READONLY_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=readonly_password="$BI_READONLY_PASSWORD" \
  --set=database_name="$POSTGRES_DB" <<-'EOSQL'
SELECT format('CREATE ROLE agent_readonly LOGIN PASSWORD %L', :'readonly_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_readonly')
\gexec
ALTER ROLE agent_readonly SET default_transaction_read_only = on;
ALTER ROLE agent_readonly SET statement_timeout = '5s';
GRANT CONNECT ON DATABASE :"database_name" TO agent_readonly;
EOSQL
