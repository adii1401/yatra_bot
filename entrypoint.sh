#!/bin/bash
set -e

echo "🗄️ Running Database Migrations..."
alembic upgrade head

echo "🚀 Starting Trip OS Server..."
exec uvicorn server:app --host 0.0.0.0 --port $PORT