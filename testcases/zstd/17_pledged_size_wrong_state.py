import _zstd
c = _zstd.ZstdCompressor()
c.compress(b"x", c.CONTINUE)
c.set_pledged_input_size(1)
