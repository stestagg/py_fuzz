import io
t = io.TextIOWrapper(io.BytesIO(b"first"))
t.read()
t.__init__(io.BytesIO(b"second"))
t.read()
t.encoding
