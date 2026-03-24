import compression.zstd as z
d = z.ZstdDecompressor().decompressobj()
d.decompress(z.compress(b"hello"))
d.decompress(b"extra")
