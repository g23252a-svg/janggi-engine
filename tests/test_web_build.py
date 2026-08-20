"""The GitHub Pages build.

The published site runs the engine in the browser under WebAssembly, so its API
layer (web/engine_api.py) is a second implementation of what server.py serves
over Flask. These tests keep the two answering the same way, and keep the build
from silently shipping a page with no engine in it.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from janggi.board import Board, CHO, HAN  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "web"))
import build_site  # noqa: E402


def load_engine_api(site_dir):
    """Import the copy inside the built site, exactly as the browser will."""
    spec = importlib.util.spec_from_file_location(
        "site_engine_api", os.path.join(site_dir, "engine_api.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["site_engine_api"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "site"
    build_site.build(out)
    return out


def test_build_produces_a_runnable_page(site):
    html = (site / "index.html").read_text(encoding="utf-8")
    assert '<script src="browser-engine.js"></script>' in html
    assert html.count("browser-engine.js") == 1, "injected twice"
    assert (site / "browser-engine.js").exists()
    assert (site / "engine_api.py").exists()
    assert (site / ".nojekyll").exists(), "Jekyll would drop the janggi/ folder"


def test_the_shipped_package_imports_on_its_own(site):
    """Import the built site's engine in a fresh process whose only `janggi` is
    the shipped one.

    The other tests here load `site/engine_api.py` from inside pytest, where the
    repository root is already on sys.path -- so `import janggi` silently
    resolves to the source tree and a module the build forgot to copy goes
    unnoticed. That is exactly what happened: adding `janggi/_version.py` and
    importing it from `__init__` broke the Pages build, every test here passed,
    and the deploy caught it. Running from the site directory in a subprocess is
    the only way this file can tell the two copies apart.
    """
    out = subprocess.run(
        [sys.executable, "-c",
         "import engine_api; "
         "b = engine_api.api_new({'cho': 'msm_s', 'han': 'msm_s'}); "
         "assert b['board'], 'no start position'"],
        cwd=site, capture_output=True, text=True, timeout=300,
        env={**os.environ, "JANGGI_NO_ACCEL": "1", "PYTHONPATH": ""},
    )
    assert out.returncode == 0, out.stderr


def test_build_ships_every_module_the_page_loads(site):
    """browser-engine.js fetches a fixed module list; the build must ship it."""
    js = (site / "browser-engine.js").read_text(encoding="utf-8")
    listed = js.split("const MODULES = [", 1)[1].split("]", 1)[0]
    names = [chunk.strip().strip('",') for chunk in listed.split(",") if chunk.strip()]
    assert names, "could not parse the module list out of browser-engine.js"
    for name in names:
        assert (site / "janggi" / f"{name}.py").exists(), f"page loads {name}.py, build omits it"


def test_build_ships_every_asset_the_page_references(site):
    """Under Pages the site lives at /<repo>/, so index.html references its
    assets relatively and a missing one is a 404 on the phone rather than a
    build error. This is the same invariant test_server.py checks for Flask,
    against the other build."""
    html = (site / "index.html").read_text(encoding="utf-8")
    refs = set(re.findall(r'(?:href|src)="(?!https?:|/|#|data:)([^"]+)"', html))
    refs |= set(re.findall(r"register\('([^']+)'\)", html))
    assert "manifest.webmanifest" in refs, "parsed nothing; the regex needs updating"
    for ref in sorted(refs):
        assert (site / ref).exists(), f"index.html references {ref}, build omits it"


def test_the_published_page_shows_the_version_not_a_jinja_placeholder(site):
    """Flask renders this template through Jinja; Pages serves it as a file. A
    placeholder the build forgets to substitute ships as literal braces."""
    from janggi import __version__

    html = (site / "index.html").read_text(encoding="utf-8")
    assert "{{" not in html and "{%" not in html, "un-substituted Jinja reached the page"
    assert __version__ in html


def test_build_ships_the_icons_the_manifest_declares(site):
    manifest = json.loads((site / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["icons"], "an installable app needs at least one icon"
    for icon in manifest["icons"]:
        assert (site / icon["src"]).exists(), f"manifest declares {icon['src']}, build omits it"


def test_the_service_worker_never_caches_an_engine_answer(site):
    """The shell is cached so the page opens offline. A cached /api/ response
    would be a stale best move presented as a fresh one, which is worse than
    an error."""
    sw = (site / "sw.js").read_text(encoding="utf-8")
    assert "/api/" in sw and "return" in sw


def test_build_excludes_modules_that_need_torch(site):
    for name in ("nn_eval.py", "nn_model.py", "train.py", "selfplay.py", "arena.py"):
        assert not (site / "janggi" / name).exists(), f"{name} would fail to import in the browser"


def test_browser_api_matches_the_server(site):
    api = load_engine_api(str(site))

    start = api.api_new({"cho": "msm_s", "han": "msm_s"})
    assert start["board"] == [
        [None if p is None else ("h" if p[1] == HAN else "c") + p[0] for p in row]
        for row in Board.standard().grid
    ]

    legal = api.api_legal({"board": start["board"], "fr": 6, "fc": 0})
    assert {(m["tr"], m["tc"]) for m in legal["moves"]} == {(5, 0), (6, 1)}

    result = api.api_analyze({"board": start["board"], "side": "cho", "time": 1.0})
    assert result["move"] is not None
    assert result["engine"] == "browser"
    assert result["depthReached"] >= 1
    opening = {m.as_tuple() for m in Board.standard().legal_moves(CHO)}
    move = result["move"]
    assert (move["fr"], move["fc"], move["tr"], move["tc"]) in opening


def test_browser_api_reports_bad_input_as_an_error_not_a_crash(site):
    api = load_engine_api(str(site))
    for payload in ('{"board": [[1]]}', '{"board": "nope"}', "{}"):
        assert "error" in api.handle("/api/analyze", payload)
    assert "error" in api.handle("/api/legal", '{"board": null}')
    assert "unknown endpoint" in api.handle("/api/nope", "{}")


def test_browser_api_handles_a_move_history(site):
    api = load_engine_api(str(site))
    out = api.api_analyze({
        "history": [{"fr": 6, "fc": 2, "tr": 6, "tc": 3, "captured": None, "side": "cho"}],
        "cho_formation": "msm_s", "han_formation": "msm_s",
        "side": "han", "time": 1.0,
    })
    assert out["move"] is not None
