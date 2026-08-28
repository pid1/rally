from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from rally import member_colors
from rally.database import init_db
from rally.routers import (
    dashboard,
    dinner_planner,
    events,
    family,
    preparedness,
    recurring_todos,
    settings,
    shopping,
    todos,
)
from rally.utils.static_version import STATIC_VERSION

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class NoCacheStaticFiles(StaticFiles):
    """Serve static files with Cache-Control: no-cache.

    Browsers must revalidate (cheap 304 via ETag) instead of heuristically
    caching assets and serving stale CSS after a deploy.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Rally",
    description="Your family command center",
    version="0.1.0",
    lifespan=lifespan,
)

# Static files
static_dir = BASE_DIR / "static"
if static_dir.is_dir():
    app.mount("/static", NoCacheStaticFiles(directory=str(static_dir)), name="static")

# Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["css_version"] = STATIC_VERSION

# Include routers
app.include_router(dashboard.router)
app.include_router(events.router)
app.include_router(todos.router)
app.include_router(dinner_planner.router)
app.include_router(family.router)
app.include_router(recurring_todos.router)
app.include_router(settings.router)
app.include_router(shopping.router)
app.include_router(preparedness.router)


@app.get("/", response_class=RedirectResponse)
def index():
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/todo", response_class=HTMLResponse)
def todo_page(request: Request):
    """Serve the todo management page."""
    return templates.TemplateResponse("todo.html", {"request": request})


@app.get("/todo/completed", response_class=HTMLResponse)
def todo_completed_page(request: Request):
    """Serve the read-only page of previously completed tasks."""
    return templates.TemplateResponse("todo_completed.html", {"request": request})


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
    """Serve the calendar page (month and agenda views)."""
    return templates.TemplateResponse("calendar.html", {"request": request})


@app.get("/shopping", response_class=HTMLResponse)
def shopping_page(request: Request):
    """Serve the shopping list page."""
    return templates.TemplateResponse("shopping.html", {"request": request})


@app.get("/shopping/purchased", response_class=HTMLResponse)
def shopping_purchased_page(request: Request):
    """Serve the read-only page of previously purchased shopping items."""
    return templates.TemplateResponse("shopping_purchased.html", {"request": request})


@app.get("/dinner-planner", response_class=HTMLResponse)
def dinner_planner_page(request: Request):
    """Serve the meal planner page."""
    return templates.TemplateResponse("dinner_planner.html", {"request": request})


@app.get("/meal-history", response_class=HTMLResponse)
def meal_history_page(request: Request):
    """Serve the meal history and reviews page."""
    return templates.TemplateResponse("meal_history.html", {"request": request})


@app.get("/meal-planner", response_class=RedirectResponse)
def meal_planner_redirect():
    """Redirect /meal-planner to /dinner-planner for convenience."""
    return RedirectResponse(url="/dinner-planner")


@app.get("/preparedness", response_class=HTMLResponse)
def preparedness_page(request: Request):
    """Serve the preparedness inventory page."""
    return templates.TemplateResponse("preparedness.html", {"request": request})


@app.get("/go-list", response_class=HTMLResponse)
def go_list_page(request: Request):
    """Serve the go list — the printable packing list, grouped by location."""
    return templates.TemplateResponse("go_list.html", {"request": request})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    """Serve the settings page.

    The member color palette is rendered into the page rather than fetched:
    it is static configuration rather than state, and server-rendering it means
    the swatches are in the first paint. That matters on a display that repaints
    as slowly as e-ink.
    """
    return templates.TemplateResponse(
        "settings.html", {"request": request, "member_palette": member_colors.PALETTE}
    )


@app.get("/styleguide", response_class=HTMLResponse)
def styleguide_page(request: Request):
    """Serve the design system reference.

    Renders every component and state from the real stylesheet, so the
    documentation cannot drift from the code the way a written spec would. It
    is unlinked from the nav — a reference for whoever is changing the CSS, not
    a page the family visits — but it ships, because a styleguide that only
    exists in development stops matching what production actually looks like.
    """
    return templates.TemplateResponse(
        "styleguide.html", {"request": request, "member_palette": member_colors.PALETTE}
    )
