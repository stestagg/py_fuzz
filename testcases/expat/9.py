from xml.parsers import expat

data = bytearray(b"<a><b/></a>")
p = expat.ParserCreate()

def start(name, attrs):
    data[:] = b"<corrupt"  # mutate backing buffer

p.StartElementHandler = start
p.Parse(data, True)