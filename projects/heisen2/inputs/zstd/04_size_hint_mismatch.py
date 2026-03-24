import compression.zstd as z
c = z.compress(b"a" * 10)
z.decompress(c, max_output_size=1)
