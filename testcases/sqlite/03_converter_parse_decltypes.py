# 3. Converter with PARSE_DECLTYPES, odd bytes payload
import sqlite3
sqlite3.register_converter("X", lambda b: (type(b).__name__, b[::-1]))
con = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES)
con.execute("create table t(a X)")
con.execute("insert into t values (?)", (b"\x00\xffxyz",))
con.execute("select a from t").fetchall()
