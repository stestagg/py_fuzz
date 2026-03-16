import re

pat = re.compile(r"(\d{1,3}\.){3}\d{1,3}")
m = pat.search("IP: 192.168.1.1 and 10.0.0.1")
all_ips = pat.findall("192.168.1.1 10.0.0.1")

email_pat = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.IGNORECASE)
emails = email_pat.findall("user@example.com, bad@, test@foo.org")
