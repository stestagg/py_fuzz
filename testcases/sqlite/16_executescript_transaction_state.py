# 16. executescript() mixed with transaction state
import sqlite3

con = sqlite3.connect(":memory:")
try:
    con.executescript("""
        begin;
        create table t(x);
        insert into t values (1);
        rollback;
        select * from t;
    """)
except Exception:
    pass
