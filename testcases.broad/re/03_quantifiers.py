import re

a = re.findall(r"a*", "aabaa")
b = re.findall(r"a+", "aabaa")
c = re.findall(r"a?", "aab")
d = re.findall(r"a{2,4}", "aaaaa")
e = re.findall(r"a{3}", "aaaa")

# greedy vs non-greedy
greedy = re.search(r"<.*>", "<a><b>")
lazy = re.search(r"<.*?>", "<a><b>")
