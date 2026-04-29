# 11. set_progress_handler — interrupt after N ticks
import sqlite3

con = sqlite3.connect(":memory:")
ticks = 0

def ph():
    global ticks
    ticks += 1
    return 1 if ticks > 2 else 0

con.set_progress_handler(ph, 1)
try:
    con.execute(
        "with recursive t(x) as (select 1 union all select x+1 from t where x<50) "
        "select sum(a.x*b.x) from t a, t b"
    ).fetchall()
except Exception:
    pass
