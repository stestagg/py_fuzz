import pickle
u = pickle.Unpickler.__new__(pickle.Unpickler)
u.load()
u.memo
