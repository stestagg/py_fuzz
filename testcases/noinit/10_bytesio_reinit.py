import io
b = io.BytesIO(b"first")
b.__init__(b"second")
b.getvalue()
b.__init__()
b.read()
