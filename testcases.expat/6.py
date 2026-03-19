from xml.parsers import expat

p = expat.ParserCreate()

def start(name, attrs):
    p.StartElementHandler = None  # remove handler mid-parse

p.StartElementHandler = start
p.Parse(b"<a><b/></a>", True)