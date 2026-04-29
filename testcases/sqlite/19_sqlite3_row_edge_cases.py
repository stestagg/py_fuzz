# 19. sqlite3.Row indexing / name lookup edge cases
import sqlite3

con = sqlite3.connect(":memory:")
con.row_factory = sqlite3.Row
con.execute("create table t(a,b)")
con.execute("insert into t values(10,20)")
r = con.execute("select a as x, b as x from t").fetchone()

try: r[99]
except Exception: pass

try: r["missing"]
except Exception: pass

tuple(r.keys())
tuple(r)
