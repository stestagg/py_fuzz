import ssl
o = ssl.SSLObject.__new__(ssl.SSLObject)
repr(o)
o.version()
o.getpeercert()
