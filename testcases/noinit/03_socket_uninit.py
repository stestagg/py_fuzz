import socket
s = socket.socket.__new__(socket.socket)
repr(s)
s.fileno()
s.getsockname()
