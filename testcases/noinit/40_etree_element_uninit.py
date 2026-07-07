import xml.etree.ElementTree as ET
e = ET.Element.__new__(ET.Element)
repr(e)
e.tag
e.append(ET.Element("child"))
list(e)
