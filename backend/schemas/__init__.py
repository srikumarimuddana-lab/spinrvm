# schemas/__init__.py
# The schemas/ package shadows the legacy schemas.py flat module.
# Re-export everything from schemas.py so existing bare `from schemas import X`
# calls keep working after the package takes over the name.
import importlib.util as _ilu
import os as _os
import sys as _sys

_flat = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "schemas.py")
_spec = _ilu.spec_from_file_location("_schemas_flat", _flat)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Inject every public name from the flat module into this package's namespace.
for _name in dir(_mod):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_mod, _name)

del _ilu, _os, _sys, _flat, _spec, _mod, _name
