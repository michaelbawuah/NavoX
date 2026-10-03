#!/usr/bin/env bash
set -euo pipefail
: "${NAVOX_DATABASE_PASSWORD:?A unique URL-safe database password is required}"
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=navox_password="$NAVOX_DATABASE_PASSWORD" <<'SQL'
CREATE ROLE navox WITH LOGIN PASSWORD :'navox_password';
CREATE DATABASE navox OWNER navox;
SQL
