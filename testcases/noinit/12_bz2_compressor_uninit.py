import bz2
c = bz2.BZ2Compressor.__new__(bz2.BZ2Compressor)
c.compress(b"payload")
c.flush()
