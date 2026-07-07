import io
class F(io.FileIO):
    __slots__ = ()
f = F.__new__(F)
repr(f)
f.seekable()
f.fileno()
