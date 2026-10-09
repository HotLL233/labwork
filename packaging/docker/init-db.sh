#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres --set=app_password="$LABWORK_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE labwork LOGIN PASSWORD %L', :'app_password') \gexec
CREATE DATABASE labwork_prod OWNER labwork;
SQL
