import compression.zstd as z
z.decompress(b"\x28\xb5\x2f\xfd" + b"\x00" * 10)
