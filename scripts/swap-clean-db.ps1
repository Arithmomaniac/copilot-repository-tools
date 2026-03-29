# Repair session-store.db: drop corrupted CST tables, VACUUM to fix freelist.
# Run this AFTER closing all Copilot CLI sessions.

$db = "$env:USERPROFILE\.copilot\session-store.db"

Write-Host "Dropping CST tables and vacuuming $db ..." -ForegroundColor Cyan

python -c @"
import sqlite3
conn = sqlite3.connect(r'$db')
conn.execute('PRAGMA writable_schema = ON')
for obj_type, name in conn.execute("SELECT type, name FROM sqlite_master WHERE name LIKE 'cst%'").fetchall():
    print(f'  Dropping {obj_type} {name}')
    try:
        conn.execute(f'DROP {obj_type.upper()} IF EXISTS [{name}]')
    except Exception:
        conn.execute(f"DELETE FROM sqlite_master WHERE name = '{name}'")
conn.execute('PRAGMA writable_schema = OFF')
conn.commit()
print('Vacuuming...')
conn.execute('VACUUM')
print('Integrity:', conn.execute('PRAGMA integrity_check').fetchone()[0])
conn.close()
"@

# Clean up stale files
Remove-Item "$db.corrupt*", "$db.bak-*" -Force -ErrorAction SilentlyContinue
Remove-Item "$env:USERPROFILE\.copilot\session-store-clean.db" -Force -ErrorAction SilentlyContinue

Write-Host "Done. Now run: uv run copilot-session-tools scan --full" -ForegroundColor Green
