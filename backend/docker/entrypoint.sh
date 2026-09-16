#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Ensuring the admin account exists..."
python seed.py

exec "$@"
