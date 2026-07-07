import io
class B(io.BufferedWriter):
    __slots__ = ()
b = B.__new__(B)
repr(b)
b.write(b"data")
b.flush()
