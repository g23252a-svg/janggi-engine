"""Assemble the static site that GitHub Pages serves.

There is deliberately no second copy of the board UI: this takes the exact
templates/index.html the Flask server renders and injects one script tag, so
the two can never drift apart. That script (browser-engine.js) intercepts the
/api/ calls and answers them from a Python interpreter compiled to WebAssembly,
with the real janggi package copied in beside it.

    python web/build_site.py site/
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# Pure-Python modules only. The Cython accelerators cannot be loaded under
# WebAssembly; janggi.board falls back automatically when they are absent.
# nn_eval / nn_model / train / selfplay / arena need torch and are not imported
# by janggi/__init__.py, so they stay out.
PACKAGE_MODULES = [
    "__init__.py", "_version.py", "board.py", "evaluate.py", "see.py",
    "search.py", "score.py", "repetition.py", "gibo.py", "book.py", "mcts.py",
    "nn_encode.py",
]

INJECT = '<script src="browser-engine.js"></script>\n</head>'


def read_version() -> str:
    """The version, without importing janggi (which would need the extensions)."""
    src = (ROOT / "janggi" / "_version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', src, re.M)
    if not match:
        raise SystemExit("no __version__ in janggi/_version.py")
    return match.group(1)


def build(out_dir: pathlib.Path) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "janggi").mkdir(parents=True)

    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    if "</head>" not in html:
        raise SystemExit("templates/index.html has no </head> to inject into")
    html = html.replace("</head>", INJECT, 1)
    # Flask renders the template through Jinja; Pages serves the file as-is, so
    # anything Jinja would have substituted has to be substituted here or it
    # ships as literal braces on the page.
    html = html.replace("{{ version or '' }}", read_version())
    if "{{" in html or "{%" in html:
        raise SystemExit(
            "templates/index.html has Jinja syntax the Pages build does not "
            "substitute; add it to build() or the braces ship to the page"
        )
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    shutil.copy2(WEB / "browser-engine.js", out_dir / "browser-engine.js")
    shutil.copy2(WEB / "engine_api.py", out_dir / "engine_api.py")
    # Installable-to-home-screen assets. index.html references these by relative
    # path so the same markup works under Flask at / and under Pages at
    # /<repo>/, which a leading slash would break.
    for asset in ("manifest.webmanifest", "sw.js",
                  "icon-180.png", "icon-192.png", "icon-512.png"):
        shutil.copy2(WEB / asset, out_dir / asset)
    for name in PACKAGE_MODULES:
        src = ROOT / "janggi" / name
        if not src.exists():
            raise SystemExit(f"missing {src}")
        shutil.copy2(src, out_dir / "janggi" / name)

    # Jekyll would otherwise skip files and directories it does not recognise.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    files = sorted(p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*") if p.is_file())
    print(f"built {out_dir} with {len(files)} files:")
    for f in files:
        print("  " + f)


if __name__ == "__main__":
    build(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "site").resolve())
