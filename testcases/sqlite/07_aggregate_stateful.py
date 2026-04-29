# 7. Aggregate class with stateful Python object lifecycle
import sqlite3
class Agg:
    def __init__(self): self.v = []
    def step(self, x): self.v.append(x); self.v.append(self)
    def finalize(self): return repr(len(self.v))
con = sqlite3.connect(":memory:")
con.create_aggregate("agg", 1, Agg)
con.execute("create table t(x)")
con.executemany("insert into t values(?)", [(1,), (2,), (3,)])
con.execute("select agg(x) from t").fetchall()
