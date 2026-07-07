import hashlib
h = hashlib.blake2b.__new__(hashlib.blake2b)
h.update(b"data")
h.digest()
h.hexdigest()
