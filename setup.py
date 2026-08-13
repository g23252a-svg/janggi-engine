"""Build config for janggi-engine: all Cython accelerators.
If Cython/compile fails at deploy time, every accelerated path falls back to
pure Python (board/evaluate/search each guard on module availability), so the
engine still runs — just slower.
"""
import pathlib
import re

from setuptools import setup, find_packages, Extension

# Read rather than import: importing janggi here would pull in the extensions
# that this file exists to build. The regex is against one line in one file
# whose only job is to hold it, and a test pins that the two agree.
_VERSION_SRC = pathlib.Path(__file__).parent / "janggi" / "_version.py"
_MATCH = re.search(r'^__version__ = "([^"]+)"', _VERSION_SRC.read_text(encoding="utf-8"), re.M)
if not _MATCH:
    raise SystemExit(f"no __version__ found in {_VERSION_SRC}")
VERSION = _MATCH.group(1)

PYX = [
    ("janggi._attack",   "janggi/_attack.pyx"),
    ("janggi._movegen",  "janggi/_movegen.pyx"),
    ("janggi._core",     "janggi/_core.pyx"),
]

try:
    from Cython.Build import cythonize
    ext_modules = cythonize(
        [Extension(name, [src]) for name, src in PYX],
        language_level=3,
        compiler_directives={"boundscheck": False, "wraparound": False, "cdivision": True},
    )
except Exception as e:  # noqa
    print(f"[setup.py] Cython unavailable; building pure-Python: {e}")
    ext_modules = []

setup(
    name="janggi-engine",
    version=VERSION,
    description="Korean chess (Janggi) engine with a Cython search core.",
    python_requires=">=3.10",
    packages=find_packages(include=["janggi*"]),
    ext_modules=ext_modules,
    extras_require={
        # README tells people to `pip install -e ".[dev]"`; without this that
        # silently installs nothing extra.
        "dev": ["pytest>=7.0", "cython>=3.0"],
        "web": ["flask>=3.0", "gunicorn>=21.0"],
    },
)
