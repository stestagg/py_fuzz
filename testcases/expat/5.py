from xml.parsers import expat

p = expat.ParserCreate()

def start(name, attrs):
    p.Parse(b"<inner/>", True)  # re-enter parser mid-callback

p.StartElementHandler = start
p.Parse(b"<a/>", True)