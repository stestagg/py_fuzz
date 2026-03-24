import compression.zstd as z
d = z.ZstdCompressionDict(b"\x00" * 8)
z.compress(b"test", dict_data=d)
