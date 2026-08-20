"""One version, reported the same way everywhere.

Before this existed the only version in the repository was `setup.py`'s
`0.3.0`, and it stayed 0.3.0 through three patches that each changed how the
engine plays. A version nobody updates is worse than no version: it tells you
the build is something it is not.

These tests pin that every place reporting a version reads the same one, and
that the packaging metadata and the Pages build cannot drift from the package.
"""

import importlib.util
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from janggi import __version__, VERSION_INFO  # noqa: E402


def test_the_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__
    assert VERSION_INFO == tuple(int(p) for p in __version__.split("."))
    assert len(VERSION_INFO) == 3


@pytest.mark.skipif(
    importlib.util.find_spec("setuptools") is None,
    reason="setup.py needs setuptools; 3.12 does not bundle it and the "
           "pure-Python CI job installs no build tools on purpose",
)
def test_setup_py_reports_the_package_version():
    """setup.py parses _version.py rather than carrying its own copy. If that
    parse ever silently fails it would fall back to something wrong, so ask
    setuptools what it actually resolved.

    Skipped where setuptools is absent -- that is an environment without
    packaging tools, not a broken version. The compiled jobs all have it."""
    out = subprocess.run(
        [sys.executable, "setup.py", "--version"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == __version__


def test_the_cli_reports_the_version():
    out = subprocess.run(
        [sys.executable, "-m", "janggi.cli", "--version"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert __version__ in out.stdout


def test_the_changelog_documents_this_version():
    """A release with no changelog entry is a release nobody can read."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (?:v)?(\d+\.\d+\.\d+)", text, re.M)
    assert headings, "CHANGELOG.md has no versioned headings"
    assert headings[0] == __version__, (
        f"CHANGELOG.md's newest entry is {headings[0]}, package is {__version__}"
    )


def test_the_health_endpoint_reports_the_version_and_whether_it_is_compiled():
    """Deployments build the extensions and fall back to pure Python if that
    fails, so 'which version' is only half the question."""
    pytest.importorskip("flask")
    import server as srv

    body = srv.app.test_client().get("/health").get_json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert isinstance(body["accel"], bool)
