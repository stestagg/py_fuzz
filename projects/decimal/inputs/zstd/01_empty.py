import compression.zstd as z
z.decompress(z.compress(b""))
