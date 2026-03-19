import re

simple = re.findall(r"cat|dog|bird", "I have a cat and a dog")
grouped = re.findall(r"(cat|dog)(fish)?", "catfish dogfish cat dog")
nested_alt = re.search(r"(a(b|c)|d(e|f))+", "abdabcdef")
