import gzip
g = gzip.GzipFile.__new__(gzip.GzipFile)
repr(g)
g.read()
g.close()
