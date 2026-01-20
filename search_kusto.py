import sqlite3
import zlib
import json

conn = sqlite3.connect('copilot_chats.db')
cursor = conn.cursor()

# Get top sessions from last 7 days
cursor.execute("""
    SELECT rs.session_id, rs.workspace_name, rs.raw_json_compressed, COUNT(m.id) as msg_count
    FROM raw_sessions rs
    JOIN messages m ON rs.session_id = m.session_id
    WHERE rs.imported_at >= date('now', '-7 days')
    GROUP BY rs.session_id
    ORDER BY msg_count DESC
    LIMIT 15
""")

sessions = cursor.fetchall()

print(f"Searching {len(sessions)} sessions for 'kusto' mentions...\n")

for session_id, workspace, compressed_json, msg_count in sessions:
    try:
        # Decompress and parse JSON
        raw_json = zlib.decompress(compressed_json).decode('utf-8')
        
        # Simple string search (case-insensitive)
        if 'kusto' in raw_json.lower():
            print(f"✓ Found 'kusto' in session: {session_id}")
            print(f"  Workspace: {workspace}")
            print(f"  Messages: {msg_count}")
            
            # Count occurrences
            count = raw_json.lower().count('kusto')
            print(f"  Mentions: {count}\n")
    except Exception as e:
        print(f"✗ Error processing {session_id}: {e}\n")

conn.close()
