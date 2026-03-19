from xml.parsers import expat

p = expat.ParserCreate()

# mutate parser mid-parse
def start(name, attrs):
    p.buffer_text = not p.buffer_text
    p.returns_unicode = not getattr(p, "returns_unicode", True)
    p.Parse(b"<a/>", True)  # re-entrant parse

p.StartElementHandler = start

# weird inputs: embedded nulls, partial chunks, final toggling
data = b"<root>\x00<a attr='\xff'/></root>"
p.Parse(data[:7], False)
p.Parse(data[7:15], False)
p.Parse(data[15:], True)