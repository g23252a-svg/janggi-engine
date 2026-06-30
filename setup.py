"""Build config for janggi-engine, including the Cython attack accelerator.
If Cython compilation fails at deploy time, the engine still runs because
board.py falls back to the pure-Python attack routine.
"""
from setuptools import setup, find_packages, Extension

try:
    from Cython.Build import cythonize
    ext_modules = cythonize(
        [Extension("janggi._attack", ["janggi/_attack.pyx"])],
        language_level=3,
        compiler_directives={"boundscheck": False, "wraparound": False, "cdivision": True},
    )
except Exception as e:  # noqa
    print(f"[setup.py] Cython unavailable; building pure-Python: {e}")
    ext_modules = []

setup(
    name="janggi-engine",
    version="0.1.0",
    description="Korean chess (Janggi) engine with alpha-beta search.",
    python_requires=">=3.10",
    packages=find_packages(include=["janggi*"]),
    ext_modules=ext_modules,
)
