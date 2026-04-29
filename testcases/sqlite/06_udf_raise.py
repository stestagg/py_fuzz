# 6. User-defined scalar function returning odd value / raising
import sqlite3
con = sqlite3.connect(":memory:")
con.create_function("f", 1, lambda x: {} if x else (_ for _ in ()).throw(ValueError("boom")))
try:
    con.execute("select f(0), f(1)").fetchall()
except Exception:
    pass
