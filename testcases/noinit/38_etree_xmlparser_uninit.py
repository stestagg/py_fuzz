import xml.etree.ElementTree as ET
p = ET.XMLParser.__new__(ET.XMLParser)
p.feed("<a/>")
p.close()
