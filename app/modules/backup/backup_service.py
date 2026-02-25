"""
Backup Module — Automated Daily Database Backup
================================================
- PostgreSQL dump via pg_dump (if available)
- Fallback: export critical tables as JSON
- Scheduled via APScheduler
"""

import os
import json
import subprocess
from datetime import datetime

from app.config import settings


async def backup_database() -> dict:
    """
    Automated database backup.
    Tries pg_dump first, falls back to JSON export.
    """
    backup_dir = "backups"
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"backup_{timestamp}"

    # Try pg_dump
    try:
        dump_file = os.path.join(backup_dir, f"{filename}.sql")
        db_url = settings.DATABASE_URL_SYNC  # Sync URL for pg_dump

        # Parse connection URL for pg_dump
        # Format: postgresql://user:pass@host:port/dbname
        if 'postgresql://' in db_url:
            result = subprocess.run(
                ['pg_dump', db_url, '-f', dump_file, '--clean', '--if-exists'],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                size_kb = os.path.getsize(dump_file) / 1024
                return {
                    'success': True,
                    'method': 'pg_dump',
                    'file': dump_file,
                    'size_kb': round(size_kb, 1),
                    'timestamp': timestamp,
                }
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Fallback: JSON export of critical data
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(settings.DATABASE_URL_SYNC)
        tables = ['users', 'transactions', 'budgets', 'goals']
        export = {}

        with engine.connect() as conn:
            for table in tables:
                try:
                    result = conn.execute(text(f"SELECT * FROM {table}"))
                    rows = [dict(row._mapping) for row in result]
                    # Convert non-serializable types
                    for row in rows:
                        for k, v in row.items():
                            if hasattr(v, 'isoformat'):
                                row[k] = v.isoformat()
                            elif hasattr(v, 'hex'):  # UUID
                                row[k] = str(v)
                    export[table] = rows
                except Exception:
                    export[table] = []

        json_file = os.path.join(backup_dir, f"{filename}.json")
        with open(json_file, 'w') as f:
            json.dump(export, f, indent=2, default=str)

        size_kb = os.path.getsize(json_file) / 1024
        return {
            'success': True,
            'method': 'json_export',
            'file': json_file,
            'size_kb': round(size_kb, 1),
            'timestamp': timestamp,
            'tables': list(export.keys()),
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def cleanup_old_backups(max_keep: int = 7):
    """Remove backup files older than max_keep count."""
    backup_dir = "backups"
    if not os.path.exists(backup_dir):
        return

    files = sorted(
        [os.path.join(backup_dir, f) for f in os.listdir(backup_dir)],
        key=os.path.getmtime,
        reverse=True,
    )

    for old_file in files[max_keep:]:
        try:
            os.remove(old_file)
        except Exception:
            pass
