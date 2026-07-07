import struct
import copy
s = struct.Struct.__new__(struct.Struct)
s.__reduce__()
copy.copy(s)
