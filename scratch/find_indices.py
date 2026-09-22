import sqlite3

def find_indices(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    query = """
    SELECT code, name, market FROM stock_master WHERE name LIKE '%코스피%' OR name LIKE '%KOSPI%'
    UNION
    SELECT code, name, 'OVERSEAS' as market FROM overseas_master WHERE name LIKE '%VIX%' OR name LIKE '%S&P%' OR name LIKE '%Nasdaq%'
    """
    cursor.execute(query)
    results = cursor.fetchall()
    for r in results:
        print(f"Code: {r[0]}, Name: {r[1]}, Market: {r[2]}")
    conn.close()

if __name__ == "__main__":
    find_indices("./data/deshin_backtester.db")
