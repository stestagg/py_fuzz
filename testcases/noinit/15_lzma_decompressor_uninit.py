import lzma
d = lzma.LZMADecompressor.__new__(lzma.LZMADecompressor)
d.decompress(b"\xfd7zXZ")
d.eof
d.check
