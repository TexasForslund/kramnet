#!/bin/sh
set -e

echo "Waiting for database..."
until python -c "
import asyncio, asyncpg, os, sys
async def check():
    url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
    try:
        conn = await asyncpg.connect(url)
        await conn.close()
    except Exception as e:
        sys.exit(1)
asyncio.run(check())
" 2>/dev/null; do
  echo "  db not ready — retrying in 2s"
  sleep 2
done

echo "Database is ready."

echo "Running migrations..."
alembic upgrade head

echo "Starting server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
