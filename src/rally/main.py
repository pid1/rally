from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from rally.database import init_db
from rally.routers import dashboard, dinner_planner, todos

BASE_DIR = Path(__file__).resolve().parent.parent.parent


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
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Templates
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Include routers
app.include_router(dashboard.router)
app.include_router(todos.router)
app.include_router(dinner_planner.router)


@app.get("/", response_class=RedirectResponse)
def index():
    """Redirect root to dashboard."""
    return RedirectResponse(url="/dashboard")


@app.get("/todo", response_class=HTMLResponse)
def todo_page(request: Request):
    """Serve the todo management page."""
    return templates.TemplateResponse("todo.html", {"request": request})


@app.get("/dinner-planner", response_class=HTMLResponse)
def dinner_planner_page(request: Request):
    """Serve the dinner planner page."""
    return templates.TemplateResponse("dinner_planner.html", {"request": request})
