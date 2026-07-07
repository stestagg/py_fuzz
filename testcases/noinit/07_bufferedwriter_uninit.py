import io
b = io.BufferedWriter.__new__(io.BufferedWriter)
repr(b)
b.write(b"data")
b.flush()
