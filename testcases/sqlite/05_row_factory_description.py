# 5. Row factory that re-enters the cursor description machinery
import sqlite3
def rf(cur, row):
    return (cur.description, tuple(row))
con = sqlite3.connect(":memory:")
con.row_factory = rf
con.execute("create table t(x,y)")
con.execute("insert into t values(1,2)")
con.execute("select * from t").fetchall()
