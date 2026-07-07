import xml.etree.ElementTree as ET
b = ET.TreeBuilder.__new__(ET.TreeBuilder)
b.start("a", {})
b.data("text")
b.end("a")
b.close()
