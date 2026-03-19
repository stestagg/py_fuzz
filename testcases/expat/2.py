from xml.parsers import expat

p = expat.ParserCreate()

# handler mutates itself + triggers entity expansion mid-parse
def entity(name):
    p.EntityDeclHandler = None  # remove handler mid-callback
    p.Parse(b"&x;", False)      # recursive entity use

def char(data):
    # flip namespace handling mid-stream (undefined-ish behaviour)
    p.returns_unicode = not getattr(p, "returns_unicode", True)

p.EntityDeclHandler = entity
p.CharacterDataHandler = char

# DTD + entity + odd chunking + invalid bytes
xml = b"""<!DOCTYPE r [
<!ENTITY x "<a>&x;</a>">
]>
<r>\x00&x;</r>"""

p.Parse(xml[:20], False)
p.Parse(xml[20:35], False)
p.Parse(xml[35:], True)