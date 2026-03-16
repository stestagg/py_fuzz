import re

doubled = re.findall(r"(\w+) \1", "the the cat sat sat on the mat mat")
html_tag = re.match(r"<([a-z]+)>.*?</\1>", "<div>content</div>")
tag_name = html_tag.group(1) if html_tag else None

nested_back = re.search(r"((a)(b))\3\2", "ababba")
