import codecs 

from .project import Project
from .paths import root_path

IGNORE_CHARS = b'\xfe\xff\x00\xc0\xbf\xfd\xe0'

def should_ignore(entry: bytes) -> bool:
    entry_bytes, _ = codecs.escape_decode(entry)
    return all(char in IGNORE_CHARS for char in entry_bytes)

async def make_dict(project: Project) -> int:
    # during build, dicts get written to project_dir/py/afl_dicts/XXXXX.dict
    dicts_dir = project.path('py', 'afl_dicts')
    dict_files = set(dicts_dir.glob('*.dict'))

    entries = set()
    for dict_file in dict_files:
        with dict_file.open('rb') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith(b'#'):
                    assert line.startswith(b'"') and line.endswith(b'"')
                    line = line[1:-1]
                    if not should_ignore(line):
                        entries.add(line)
    
    common_dict = root_path('helpers', 'python.dict')
    for line in common_dict.read_bytes().splitlines():
        if line.startswith(b'#') or not line.strip():
            continue
        assert line.startswith(b'"') and line.endswith(b'"'), line
        line = line[1:-1]
        entries.add(line.strip())

    output = []
    for entry in sorted(entries):
        if entry.strip():
            out_line = b'"' + entry + b'"'
            output.append(out_line)

    dest_file = project.path('py', 'combined.dict')
    dest_file.write_bytes(b'\n'.join(output) + b'\n')
    return len(output)