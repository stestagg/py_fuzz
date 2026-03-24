import compression.zstd as z
c1 = z.compress(b"a")
c2 = z.compress(b"b")
z.decompress(c1 + c2)
