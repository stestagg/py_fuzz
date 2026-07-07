import ssl
c = ssl.SSLContext.__new__(ssl.SSLContext)
repr(c)
c.set_ciphers("ALL")
c.options
