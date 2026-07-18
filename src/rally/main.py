from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from rally.database import init_db
from rally.routers import dashboard, dinner_planner, family, recurring_todos, settings, todos
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
app.include_router(todos.router)
app.include_router(dinner_planner.router)
app.include_router(family.router)
app.include_router(recurring_todos.router)
app.include_router(settings.router)


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


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    """Serve the settings page."""
    return templates.TemplateResponse("settings.html", {"request": request})
