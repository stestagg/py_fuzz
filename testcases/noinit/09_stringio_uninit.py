import io
s = io.StringIO.__new__(io.StringIO)
s.write("x")
s.getvalue()
s.seek(0)
s.read()
