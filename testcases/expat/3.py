from xml.parsers import expat

p = expat.ParserCreate(namespace_separator="|")
p.ordered_attributes = True
p.specified_attributes = True

def start(name, attrs):
    print(name, attrs)

def xml_decl(version, encoding, standalone):
    print(version, encoding, standalone)

p.StartElementHandler = start
p.XmlDeclHandler = xml_decl

p.Parse(b'<?xml version="1.0"?><r xmlns:x="u"><x:a b="1" c="2"/></r>', True)