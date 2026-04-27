#!/bin/bash
set -e

# Use the PORT provided by Render, or default to 10000
APP_PORT=${PORT:-10000}

echo "🗄️ Running Database Migrations..."
# This will now use the corrected URL from env.py
alembic upgrade head

echo "🚀 Starting Trip OS Server on port $APP_PORT..."
exec uvicorn server:app --host 0.0.0.0 --port $APP_PORT --workers 1