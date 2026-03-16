import re

m = re.match(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})", "2024-03-15")
year = m.group("year")
month = m.group("month")
d = m.groupdict()

backreference = re.search(r"(?P<word>\w+) (?P=word)", "the the end")
