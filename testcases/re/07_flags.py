import re

ci = re.findall(r"hello", "Hello HELLO hello", re.IGNORECASE)
ml = re.findall(r"^\w+", "line1\nline2\nline3", re.MULTILINE)
dot = re.search(r"a.b", "a\nb", re.DOTALL)
verbose = re.match(r"""
    (\d+)   # integer part
    \.      # decimal point
    (\d+)   # fractional part
""", "3.14", re.VERBOSE)
combined = re.findall(r"^hello$", "HELLO\nhello", re.IGNORECASE | re.MULTILINE)
