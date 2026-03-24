import _zstd
zd = _zstd.ZstdDict(b"\x00" * 8, is_raw=True)
c = _zstd.ZstdCompressor(zstd_dict=zd.as_undigested_dict)
c.compress(b"abc", c.FLUSH_FRAME)
