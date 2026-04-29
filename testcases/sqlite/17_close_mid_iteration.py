# 17. Close connection mid-iteration — cursor/connection lifecycle stress
import sqlite3

con = sqlite3.connect(":memory:")
cur = con.cursor()
cur.execute("create table t(x)")
cur.executemany("insert into t values(?)", [(1,), (2,), (3,)])
cur.execute("select x from t")
next(cur)
con.close()
try:
    next(cur)
except Exception:
    pass
