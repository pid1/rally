"""The one Jinja environment every page is rendered through.

Every page template extends ``templates/base.html``, so the environment it is
rendered with has to be the same one everywhere — the Dashboard used to be
filled in with ``str.replace`` and had no Jinja environment at all, which is
why it could not share a layout.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from rally.utils.static_version import STATIC_VERSION

BASE_DIR = Path(__file__).resolve().parent.parent.parent

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["css_version"] = STATIC_VERSION
