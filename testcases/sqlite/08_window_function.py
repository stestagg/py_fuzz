# 8. Window function callbacks with inverse/value/finalize
import sqlite3
class Win:
    def __init__(self): self.n = 0
    def step(self, x): self.n += x
    def inverse(self, x): self.n -= x
    def value(self): return self.n
    def finalize(self): return self.n
con = sqlite3.connect(":memory:")
con.create_window_function("w", 1, Win)
con.execute("create table t(x)")
con.executemany("insert into t values(?)", [(1,), (2,), (3,)])
con.execute("select w(x) over (rows between 1 preceding and current row) from t").fetchall()
