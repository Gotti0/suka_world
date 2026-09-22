import sqlite3

def explore_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # List all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"Tables: {tables}")
    
    for table in tables:
        print(f"\n--- Table: {table} ---")
        # Get column info
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()
        for col in columns:
            print(f"  Column: {col[1]} ({col[2]})")
        
        # Get count
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  Row Count: {count}")
        
        # Get sample
        cursor.execute(f"SELECT * FROM {table} LIMIT 1")
        sample = cursor.fetchone()
        print(f"  Sample: {sample}")

    conn.close()

if __name__ == "__main__":
    explore_db("./data/deshin_backtester.db")
