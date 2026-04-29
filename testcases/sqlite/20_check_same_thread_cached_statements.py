# 20. check_same_thread=False + tiny cached_statements — statement cache stress
import sqlite3, threading

con = sqlite3.connect(":memory:", check_same_thread=False, cached_statements=1)
con.execute("create table t(x)")
con.executemany("insert into t values(?)", [(i,) for i in range(5)])

errs = []

def worker(n):
    try:
        for _ in range(20):
            con.execute(f"select x from t where x = {n}").fetchall()
    except Exception as e:
        errs.append(type(e).__name__)

ts = [threading.Thread(target=worker, args=(i % 5,)) for i in range(2)]
[t.start() for t in ts]
[t.join() for t in ts]
