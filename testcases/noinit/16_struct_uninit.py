import struct
s = struct.Struct.__new__(struct.Struct)
repr(s)
s.size
s.pack(1)
s.unpack(b"")
