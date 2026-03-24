import _zstd
c = _zstd.ZstdCompressor()
blob = c.compress(b"A" * 1000, c.FLUSH_FRAME)

d = _zstd.ZstdDecompressor()
d.decompress(blob, max_length=1)
d.decompress(b"", max_length=1)
d.decompress(b"", max_length=1)
