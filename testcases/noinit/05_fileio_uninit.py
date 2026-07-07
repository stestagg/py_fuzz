import io
f = io.FileIO.__new__(io.FileIO)
repr(f)
f.fileno()
f.seekable()
f.writable()
