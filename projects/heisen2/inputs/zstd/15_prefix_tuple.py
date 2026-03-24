import _zstd
zd = _zstd.ZstdDict(b"prefix-data", is_raw=True)
c = _zstd.ZstdCompressor(zstd_dict=zd.as_prefix)
c.compress(b"hello", c.FLUSH_FRAME)
