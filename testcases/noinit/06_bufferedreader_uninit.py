import io
b = io.BufferedReader.__new__(io.BufferedReader)
repr(b)
b.peek()
b.read1(4)
b.read(8)
