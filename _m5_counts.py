import sqlite3

db = r"C:\Users\skw05\.proactive-mcp\m5-smoke\proactive.db"
con = sqlite3.connect(db)
for state, n in con.execute(
    "SELECT state, COUNT(*) FROM situations GROUP BY state ORDER BY state"
):
    print(f"{state}: {n}")
for row in con.execute(
    "SELECT id, situation_type, state, priority, detected_at, delivered_at"
    " FROM situations ORDER BY id"
):
    print(row)
con.close()
