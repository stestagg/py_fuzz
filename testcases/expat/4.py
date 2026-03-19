from xml.parsers import expat

p = expat.ParserCreate(encoding="utf-16")

def start(name, attrs):
    # mutate parser mid-parse
    p.CharacterDataHandler = lambda data: p.Parse(data, 0)

p.StartElementHandler = start

# feed odd chunks, including invalid boundaries
data = b"\xff\xfe<\x00r\x00><\x00a\x00>\x00x\x00</\x00a\x00></\x00r\x00>"
for i in range(len(data)):
    try:
        p.Parse(data[i:i+1], i == len(data) - 1)
    except Exception:
        pass