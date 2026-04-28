from datetime import datetime, timezone
from typing import Any

from app.core.runtime import ensure_local_packages

ensure_local_packages()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config.settings import get_settings
from app.services.agent import run_agent


SETTINGS = get_settings()

app = FastAPI(title="Agente de Tickets")
ticket_store: list[dict[str, Any]] = []

if SETTINGS.public_dir.exists():
    app.mount("/static", StaticFiles(directory=SETTINGS.public_dir), name="static")


class AnalyzeTicketRequest(BaseModel):
    ticket: str


def create_ticket_id() -> str:
    next_number = len(ticket_store) + 1
    return f"TCK-{next_number:04d}"


def summarize_ticket(ticket_record: dict[str, Any]) -> dict[str, Any]:
    result = ticket_record["result"]
    ticket_analysis = result["ticket_analysis"]
    technical_analysis = result["technical_analysis"]

    return {
        "id": ticket_record["id"],
        "ticket": ticket_record["ticket"],
        "created_at": ticket_record["created_at"],
        "status": ticket_record["status"],
        "repository": result["repository"],
        "problem_type": ticket_analysis["problem_type"],
        "complexity": technical_analysis["complexity"],
        "needs_code_context": ticket_analysis["needs_code_context"],
        "suspected_areas": ticket_analysis["suspected_areas"],
        "suggested_reply": technical_analysis["suggested_reply"],
    }


@app.get("/")
async def index() -> FileResponse:
    index_path = SETTINGS.public_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(index_path)


@app.get("/app.js")
async def app_js() -> FileResponse:
    return FileResponse(SETTINGS.public_dir / "app.js", media_type="application/javascript")


@app.get("/styles.css")
async def styles_css() -> FileResponse:
    return FileResponse(SETTINGS.public_dir / "styles.css", media_type="text/css")


@app.get("/api/tickets")
async def list_tickets() -> list[dict[str, Any]]:
    return [summarize_ticket(ticket) for ticket in ticket_store]


@app.post("/api/tickets", status_code=201)
async def analyze_ticket(request: AnalyzeTicketRequest) -> dict[str, Any]:
    ticket_text = request.ticket.strip()
    if not ticket_text:
        raise HTTPException(status_code=400, detail="The ticket field is required.")

    try:
        result = await run_agent(ticket_text)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(error)) from error

    record = {
        "id": create_ticket_id(),
        "ticket": ticket_text,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "analyzed",
        "result": result,
    }
    ticket_store.insert(0, record)
    return record


@app.get("/api/tickets/{ticket_id}")
async def get_ticket(ticket_id: str) -> dict[str, Any]:
    ticket = next((item for item in ticket_store if item["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    return ticket


@app.post("/api/tickets/{ticket_id}/confirm")
async def confirm_ticket(ticket_id: str) -> dict[str, Any]:
    ticket = next((item for item in ticket_store if item["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found.")

    ticket["status"] = "confirmed"
    ticket["confirmed_at"] = datetime.now(timezone.utc).isoformat()
    return ticket


def main() -> None:
    import uvicorn

    uvicorn.run("app.web.main:app", host=SETTINGS.host, port=SETTINGS.port)


if __name__ == "__main__":
    main()
