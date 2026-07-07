import lzma
c = lzma.LZMACompressor.__new__(lzma.LZMACompressor)
c.compress(b"payload")
c.flush()
