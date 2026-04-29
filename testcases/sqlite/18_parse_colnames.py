# 18. PARSE_COLNAMES with type alias in column name
import sqlite3

sqlite3.register_converter("X", lambda b: ("X", bytes(b)))
con = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_COLNAMES)
row = con.execute('select cast(x\'00ff41\' as blob) as "c [X]"').fetchone()
