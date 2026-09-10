"""Runtime is Harem Link Bridge 1.4.19 exe bytecode (see sibling .exe.pyc)."""
import marshal
from pathlib import Path

_code = marshal.loads(Path(__file__).with_suffix(".exe.pyc").read_bytes())
exec(_code, globals())
