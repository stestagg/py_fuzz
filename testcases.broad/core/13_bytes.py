b1 = b"hello"
b2 = b'\x00\xff\xfe\x80'
b3 = bytes([72, 101, 108, 108, 111])
b4 = bytearray(b"mutable")

joined = b1 + b2
sliced = b3[1:4]
decoded = b1.decode("utf-8")
hexed = b2.hex()

b4[0] = 77
view = memoryview(bytes(b4))
