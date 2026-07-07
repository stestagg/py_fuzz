import ssl
c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
c.__init__(ssl.PROTOCOL_TLS_SERVER)
c.set_ciphers("ALL")
c.options
