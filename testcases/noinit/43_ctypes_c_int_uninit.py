import ctypes
i = ctypes.c_int.__new__(ctypes.c_int)
repr(i)
i.value
i.value = 5
