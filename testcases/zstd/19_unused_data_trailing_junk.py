import _zstd
c = _zstd.ZstdCompressor()
blob = c.compress(b"abc", c.FLUSH_FRAME) + b"TRAIL"

d = _zstd.ZstdDecompressor()
d.decompress(blob)
d.unused_data
