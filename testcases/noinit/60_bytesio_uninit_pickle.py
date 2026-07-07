import io
import pickle
b = io.BytesIO.__new__(io.BytesIO)
b.__reduce__()
pickle.dumps(b)
