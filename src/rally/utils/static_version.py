"""Static asset versioning for cache busting.

Browsers (Safari in particular) heuristically cache /static/styles.css and
keep serving stale copies after a deploy. Appending a content hash to the
stylesheet URL forces a fresh fetch whenever the file actually changes.
"""

import hashlib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
STATIC_DIR = BASE_DIR / "static"


def _compute_version() -> str:
    """Return a short content hash of the stylesheet."""
    css_path = STATIC_DIR / "styles.css"
    if not css_path.is_file():
        return "0"
    return hashlib.md5(css_path.read_bytes()).hexdigest()[:12]


# Computed once at import time; deploys restart the app, which refreshes it.
STATIC_VERSION = _compute_version()
