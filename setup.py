"""Build config for janggi-engine: all Cython accelerators.
If Cython/compile fails at deploy time, every accelerated path falls back to
pure Python (board/evaluate/search each guard on module availability), so the
engine still runs — just slower.
"""
from setuptools import setup, find_packages, Extension

PYX = [
    ("janggi._attack",   "janggi/_attack.pyx"),
    ("janggi._movegen",  "janggi/_movegen.pyx"),
    ("janggi._fasteval", "janggi/_fasteval.pyx"),
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
    version="0.2.0",
    description="Korean chess (Janggi) engine with a Cython search core.",
    python_requires=">=3.10",
    packages=find_packages(include=["janggi*"]),
    ext_modules=ext_modules,
)
