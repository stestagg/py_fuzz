import io
b = io.BufferedRWPair.__new__(io.BufferedRWPair)
repr(b)
b.read(4)
b.write(b"x")
