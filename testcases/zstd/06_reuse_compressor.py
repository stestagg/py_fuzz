import compression.zstd as z
c = z.ZstdCompressor().compressobj()
c.compress(b"a")
c.flush()
c.compress(b"b")
