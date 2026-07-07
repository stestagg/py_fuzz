import socket
import copy
s = socket.socket.__new__(socket.socket)
s.__reduce__()
copy.copy(s)
