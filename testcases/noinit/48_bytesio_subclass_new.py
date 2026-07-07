import io
class B(io.BytesIO):
    __slots__ = ()
b = B.__new__(B)
b.write(b"x")
b.getvalue()
b.read()
