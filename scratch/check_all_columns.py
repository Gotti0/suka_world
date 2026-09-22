import sqlite3

conn = sqlite3.connect("./data/deshin_backtester.db")
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cursor.fetchall()]
print(f"Tables: {tables}")

for t in tables:
    cursor.execute(f"PRAGMA table_info({t})")
    cols = [c[1] for c in cursor.fetchall()]
    print(f"{t}: {cols}")
conn.close()
