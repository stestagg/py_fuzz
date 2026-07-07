import cProfile
p = cProfile.Profile.__new__(cProfile.Profile)
p.enable()
p.disable()
p.getstats()
