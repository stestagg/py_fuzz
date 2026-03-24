import compression.zstd as z
c = z.compress(b"\x00" * 100)
d = z.ZstdDecompressor().decompressobj()
for i in range(len(c)):
    d.decompress(c[i:i+1])
