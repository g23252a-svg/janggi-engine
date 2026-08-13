"""The version, in one place.

Everything that reports a version reads it from here -- the package, setup.py,
the CLI, the web API and the board UI -- because the one thing worse than an
unversioned build is three places claiming different versions. A test pins that
agreement.
"""

__version__ = "1.0.0"

#: (major, minor, patch), for callers that want to compare rather than print.
VERSION_INFO = tuple(int(part) for part in __version__.split("."))
