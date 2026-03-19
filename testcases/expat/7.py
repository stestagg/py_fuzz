from xml.parsers import expat

p = expat.ParserCreate()
p.Parse(b"<a>\x00", False)
p.Parse(b"</a>", True)