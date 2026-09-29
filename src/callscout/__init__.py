from importlib.metadata import version as _v
try:
    __version__ = _v("callscout")
except Exception:
    __version__ = "0.1.0"
