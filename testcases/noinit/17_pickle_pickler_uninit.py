import pickle
p = pickle.Pickler.__new__(pickle.Pickler)
p.dump([1, 2, 3])
p.memo
