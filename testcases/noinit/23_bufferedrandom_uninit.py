import io
b = io.BufferedRandom.__new__(io.BufferedRandom)
repr(b)
b.read(4)
b.write(b"x")
b.seek(0)
