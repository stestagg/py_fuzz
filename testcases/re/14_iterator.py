import re

# finditer returns an iterator of match objects
text = "one 1 two 2 three 3"
for m in re.finditer(r"(\w+)\s(\d)", text):
    word = m.group(1)
    num = m.group(2)
    start, end = m.span()

# overlapping simulation via pos argument
pat = re.compile(r"aa")
s = "aaaa"
pos = 0
matches = []
while m := pat.search(s, pos):
    matches.append(m.start())
    pos = m.start() + 1
