import re

start = re.findall(r"^\w+", "hello\nworld", re.MULTILINE)
end = re.findall(r"\w+$", "hello\nworld", re.MULTILINE)
word_boundary = re.findall(r"\bword\b", "word words swordfish word")
string_start = re.match(r"\Ahello", "hello world")
string_end = re.search(r"world\Z", "hello world")
