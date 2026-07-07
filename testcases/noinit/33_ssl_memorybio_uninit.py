import ssl
b = ssl.MemoryBIO.__new__(ssl.MemoryBIO)
b.write(b"data")
b.read()
b.pending
