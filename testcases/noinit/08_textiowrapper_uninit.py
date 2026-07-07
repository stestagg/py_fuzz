import io
t = io.TextIOWrapper.__new__(io.TextIOWrapper)
repr(t)
t.write("hello")
t.readline()
t.encoding
