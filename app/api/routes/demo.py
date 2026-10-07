from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import IngestionStatus, PolicyStatus
from app.domain.models import IngestionRun, Policy, PolicySection
from app.domain.schemas import CorpusStatus
from app.infrastructure.auth import AuthContext, require_roles
from app.infrastructure.database import get_session

router = APIRouter(tags=["demo"])
ViewerContext = Annotated[
    AuthContext,
    Depends(require_roles("admin", "analyst", "reviewer", "viewer")),
]
DatabaseSession = Annotated[Session, Depends(get_session)]

DEMO_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Compliance review</title>
  <style>
    :root { color-scheme: light; }
    body { margin: 0; font: 16px/1.45 system-ui, sans-serif; background: #f4f1ea; color: #1c1915; }
    header, main { max-width: 1100px; margin: 0 auto; padding: 1rem 1.25rem; }
    header { border-bottom: 1px solid #d9d1c3; }
    h1 { font-size: 1.4rem; margin: 0 0 0.25rem; }
    h2 { font-size: 1.05rem; margin: 0 0 0.75rem; }
    p { margin: 0.25rem 0; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
    section { background: #fffdf8; border: 1px solid #d9d1c3; padding: 1rem; }
    label { display: block; margin: 0.6rem 0 0.2rem; font-size: 0.85rem; }
    input, select, textarea, button { font: inherit; }
    input, select, textarea { width: 100%; box-sizing: border-box; padding: 0.4rem; }
    textarea { min-height: 6rem; }
    button { margin-top: 0.75rem; padding: 0.4rem 0.8rem; }
    .row { display: flex; gap: 0.5rem; }
    .row button { flex: 1; }
    .muted { color: #5c564c; }
    .hit, .event { border-top: 1px solid #eee6d8; padding: 0.6rem 0; }
    .error { color: #8a1f1f; }
    @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>Compliance review</h1>
    <p class="muted" id="corpus">Loading corpus…</p>
  </header>
  <main class="grid">
    <section>
      <h2>Search policy sections</h2>
      <form id="search-form">
        <label for="q">Query</label>
        <input id="q" name="q" required value="nitrogen oxides">
        <label for="mode">Mode</label>
        <select id="mode" name="mode">
          <option value="keyword" selected>keyword</option>
          <option value="fulltext">fulltext</option>
          <option value="hybrid">hybrid</option>
          <option value="embedding">embedding</option>
        </select>
        <label for="as-of">As of</label>
        <input id="as-of" name="as_of" type="date" required>
        <button type="submit">Search</button>
      </form>
      <div id="search-results"></div>
    </section>
    <section>
      <h2>Open a case</h2>
      <form id="case-form">
        <label for="title">Document title</label>
        <input id="title" required value="Stack inventory">
        <label for="source">Source</label>
        <input id="source" required value="demo">
        <label for="content">Document text</label>
        <textarea id="content" required>The inventory covers nitrogen oxides at the stack.</textarea>
        <button type="submit">Submit for review</button>
      </form>
      <div id="case-view"></div>
      <form id="review-form" hidden>
        <label for="rationale">Review rationale</label>
        <textarea id="rationale" minlength="10" required>The cited section matches the inventory language.</textarea>
        <div class="row">
          <button type="button" id="approve">Approve</button>
          <button type="button" id="reject">Reject</button>
        </div>
      </form>
      <h2>Audit</h2>
      <div id="audit-log" class="muted">No case selected.</div>
    </section>
  </main>
  <script>
    const asOf = document.querySelector("#as-of");
    asOf.value = new Date().toISOString().slice(0, 10);
    let caseId = null;

    function text(value) {
      return value == null || value === "" ? "—" : String(value);
    }

    async function readJson(response) {
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = body.detail || response.statusText;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      return body;
    }

    async function loadCorpus() {
      const status = await readJson(await fetch("/api/v1/corpus"));
      const version = status.dataset_version ? "Dataset " + status.dataset_version : "No dataset manifest loaded";
      const dates = status.publication_date_min
        ? status.publication_date_min + " to " + status.publication_date_max
        : "no publication dates";
      document.querySelector("#corpus").textContent =
        status.active_policies + " active policies, " + status.sections + " sections. " + version + ". " + dates + ".";
    }

    document.querySelector("#search-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const results = document.querySelector("#search-results");
      results.innerHTML = "";
      const params = new URLSearchParams({
        q: document.querySelector("#q").value,
        mode: document.querySelector("#mode").value,
        as_of: asOf.value,
        limit: "5",
      });
      try {
        const payload = await readJson(await fetch("/api/v1/search?" + params));
        if (!payload.results.length) {
          results.textContent = "No sections matched.";
          return;
        }
        for (const hit of payload.results) {
          const block = document.createElement("article");
          block.className = "hit";
          block.innerHTML = "<strong></strong><p></p><p class='muted'></p>";
          block.querySelector("strong").textContent = hit.name + " v" + hit.version + " " + hit.section_ref;
          block.querySelector("p").textContent = hit.content.slice(0, 420);
          block.querySelector(".muted").textContent =
            "score " + hit.score.toFixed(3) + " · lexical " + hit.lexical_score.toFixed(3) +
            " · embedding " + hit.embedding_score.toFixed(3);
          results.append(block);
        }
      } catch (error) {
        results.innerHTML = "<p class='error'></p>";
        results.querySelector("p").textContent = error.message;
      }
    });

    async function ensurePrompt() {
      const prompts = await readJson(await fetch("/api/v1/prompts"));
      const active = prompts.find((prompt) => prompt.active);
      if (active) return active.id;
      const created = await readJson(await fetch("/api/v1/prompts", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: "demo-review",
          system_prompt: "Return a structured analysis grounded only in retrieved policy evidence.",
          active: true,
        }),
      }));
      return created.id;
    }

    async function showCase(id) {
      const caseRecord = await readJson(await fetch("/api/v1/cases/" + id));
      const view = document.querySelector("#case-view");
      const citations = (caseRecord.citations || []).map((item) =>
        item.section_ref + ": " + item.quote).join("; ");
      view.innerHTML = "<div class='hit'><p></p><p></p><p></p><p></p><p></p><p></p></div>";
      const lines = view.querySelectorAll("p");
      lines[0].textContent = "Status: " + caseRecord.status;
      lines[1].textContent = "Outcome: " + text(caseRecord.outcome) + " · confidence " + text(caseRecord.confidence);
      lines[2].textContent = "Rationale: " + text(caseRecord.rationale);
      lines[3].textContent = "Citations: " + (citations || "—");
      lines[4].textContent = "Validation: " + ((caseRecord.validation_errors || []).join(", ") || "—");
      lines[5].textContent = "Injection signals: " + (caseRecord.injection_signals || []).length +
        " · Redactions recorded: " + (caseRecord.redaction_count == null ? "not recorded" : caseRecord.redaction_count);
      document.querySelector("#review-form").hidden = caseRecord.status !== "review_required";
      const audit = await readJson(await fetch("/api/v1/cases/" + id + "/audit"));
      const log = document.querySelector("#audit-log");
      log.innerHTML = "";
      log.classList.remove("muted");
      if (!audit.length) {
        log.textContent = "No audit events.";
        return caseRecord;
      }
      for (const event of audit) {
        const row = document.createElement("article");
        row.className = "event";
        row.textContent = event.created_at + " · " + event.event_type + " · " + event.actor_id;
        log.append(row);
      }
      return caseRecord;
    }

    document.querySelector("#case-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const view = document.querySelector("#case-view");
      view.textContent = "Submitting…";
      try {
        const documentRecord = await readJson(await fetch("/api/v1/documents", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            title: document.querySelector("#title").value,
            source: document.querySelector("#source").value,
            content: document.querySelector("#content").value,
          }),
        }));
        const promptId = await ensurePrompt();
        const created = await readJson(await fetch("/api/v1/cases", {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-idempotency-key": crypto.randomUUID(),
          },
          body: JSON.stringify({ document_id: documentRecord.id, prompt_template_id: promptId }),
        }));
        caseId = created.id;
        let record = created;
        for (let attempt = 0; attempt < 20 && ["queued", "analyzing"].includes(record.status); attempt += 1) {
          view.textContent = "Status: " + record.status + ". Waiting for the analysis worker.";
          await new Promise((resolve) => setTimeout(resolve, 1000));
          record = await showCase(caseId);
        }
        await showCase(caseId);
      } catch (error) {
        view.innerHTML = "<p class='error'></p>";
        view.querySelector("p").textContent = error.message;
      }
    });

    async function review(decision) {
      if (!caseId) return;
      const rationale = document.querySelector("#rationale").value;
      await readJson(await fetch("/api/v1/cases/" + caseId + "/reviews", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ decision, rationale }),
      }));
      await showCase(caseId);
    }
    document.querySelector("#approve").addEventListener("click", () => review("approve").catch((error) => {
      document.querySelector("#case-view").textContent = error.message;
    }));
    document.querySelector("#reject").addEventListener("click", () => review("reject").catch((error) => {
      document.querySelector("#case-view").textContent = error.message;
    }));

    loadCorpus().catch((error) => {
      document.querySelector("#corpus").textContent = error.message;
    });
  </script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse)
def demo_page() -> HTMLResponse:
    return HTMLResponse(DEMO_PAGE)


@router.get("/api/v1/corpus", response_model=CorpusStatus)
def corpus_status(_context: ViewerContext, session: DatabaseSession) -> CorpusStatus:
    policies = session.scalar(select(func.count()).select_from(Policy)) or 0
    active = (
        session.scalar(
            select(func.count()).select_from(Policy).where(Policy.status == PolicyStatus.ACTIVE)
        )
        or 0
    )
    sections = session.scalar(select(func.count()).select_from(PolicySection)) or 0
    latest = session.scalar(
        select(IngestionRun)
        .where(IngestionRun.status == IngestionStatus.SUCCEEDED)
        .order_by(IngestionRun.finished_at.desc())
    )
    earliest, latest_date = session.execute(
        select(func.min(Policy.publication_date), func.max(Policy.publication_date))
    ).one()
    return CorpusStatus(
        policies=policies,
        active_policies=active,
        sections=sections,
        dataset_version=None if latest is None else latest.dataset_version,
        publication_date_min=_as_date(earliest),
        publication_date_max=_as_date(latest_date),
    )


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return None
