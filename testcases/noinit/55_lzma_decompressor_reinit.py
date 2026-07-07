import lzma
d = lzma.LZMADecompressor()
d.__init__()
d.decompress(b"\xfd7zXZ")
d.eof
