import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.__init__(socket.AF_INET, socket.SOCK_DGRAM)
repr(s)
