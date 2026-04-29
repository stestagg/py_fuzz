# 12. set_trace_callback — raise inside callback
import sqlite3

con = sqlite3.connect(":memory:")
seen = []

def trace(sql):
    seen.append(sql)
    if len(seen) == 2:
        raise ValueError("trace boom")

sqlite3.enable_callback_tracebacks(True)
con.set_trace_callback(trace)

try:
    con.execute("create table t(x)")
    con.execute("insert into t values (1)")
except Exception:
    pass
