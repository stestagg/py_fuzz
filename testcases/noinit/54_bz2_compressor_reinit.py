import bz2
c = bz2.BZ2Compressor(9)
c.compress(b"payload")
c.__init__(1)
c.compress(b"more")
c.flush()
