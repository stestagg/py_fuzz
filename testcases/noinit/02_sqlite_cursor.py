import sqlite3
cur = sqlite3.Cursor.__new__(sqlite3.Cursor)
cur.execute("select 1")
cur.fetchone()
