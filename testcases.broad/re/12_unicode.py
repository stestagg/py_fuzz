import re

# Unicode word chars and categories
words = re.findall(r"\w+", "café naïve résumé")
digits = re.findall(r"\d+", "٣٤٥ 123 ４５６")

# Unicode flag (default in Python 3 for str patterns)
greek = re.findall(r"[α-ω]+", "αβγ hello δεζ")
emoji_seq = re.search(r"\U0001F600", "\U0001F600 smile")

# Bytes pattern for comparison
byte_pat = re.findall(rb"\w+", b"hello world")
