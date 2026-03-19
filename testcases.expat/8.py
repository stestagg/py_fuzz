from xml.parsers import expat

p = expat.ParserCreate(encoding="utf-8")
p.Parse(b"<a>\xff</a>", True)