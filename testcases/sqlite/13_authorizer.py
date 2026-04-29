# 13. set_authorizer — deny reads
import sqlite3

con = sqlite3.connect(":memory:")

def auth(action, p1, p2, db, src):
    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ else sqlite3.SQLITE_OK

con.execute("create table t(x)")
con.execute("insert into t values (123)")
con.set_authorizer(auth)

try:
    con.execute("select x from t").fetchall()
except Exception:
    pass
