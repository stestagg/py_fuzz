import _zstd
_zstd.finalize_dict(b"\x00" * 8, b"abcdef", (3, 4), 64, 1)
