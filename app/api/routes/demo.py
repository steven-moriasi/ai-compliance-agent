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
    .wide { grid-column: 1 / -1; }
    .answer { font-size: 1.05rem; margin: 0.75rem 0; }
    .quote { border-left: 3px solid #b9ab92; padding-left: 0.6rem; margin: 0.6rem 0; }
    .quote p:first-child { font-style: italic; }
    details { margin-top: 0.75rem; }
    summary { cursor: pointer; }
    @media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>Compliance review</h1>
    <p class="muted" id="corpus">Loading corpus…</p>
  </header>
  <main class="grid">
    <section class="wide">
      <h2>Ask the policies</h2>
      <form id="question-form">
        <label for="question">Question</label>
        <textarea id="question" required minlength="3" maxlength="1000">How must nitrogen oxides at the stack be reported?</textarea>
        <label for="question-as-of">Policies in force on</label>
        <input id="question-as-of" type="date" required>
        <button type="submit">Ask</button>
      </form>
      <div id="question-view"></div>
    </section>
    <section>
      <h2>Search policy sections</h2>
      <form id="search-form">
        <label for="q">Query</label>
        <input id="q" name="q" required value="nitrogen oxides">
        <label for="mode">Mode</label>
        <select id="mode" name="mode">
          <option value="" selected>server default</option>
          <option value="keyword">keyword</option>
          <option value="fulltext">fulltext</option>
          <option value="vector">vector</option>
          <option value="hybrid">hybrid</option>
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
    document.querySelector("#question-as-of").value = asOf.value;
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
        as_of: asOf.value,
        limit: "5",
      });
      const mode = document.querySelector("#mode").value;
      if (mode) params.set("mode", mode);
      results.textContent = "Searching…";
      try {
        const payload = await readJson(await fetch("/api/v1/search?" + params));
        results.textContent = "";
        if (!payload.results.length) {
          results.textContent = "No sections matched (" + payload.mode + ").";
          return;
        }
        const used = document.createElement("p");
        used.className = "muted";
        used.textContent = "Mode: " + payload.mode;
        results.append(used);
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
        history.replaceState(null, "", "#case=" + caseId);
        await followCase(caseId);
      } catch (error) {
        view.innerHTML = "<p class='error'></p>";
        view.querySelector("p").textContent = error.message;
      }
    });

    const PENDING = ["queued", "analyzing"];
    const FOLLOW_LIMIT_SECONDS = 600;

    function noteStatus(record, note) {
      const line = document.querySelector("#case-view p");
      if (line) line.textContent = "Status: " + record.status + " · " + note;
    }

    async function followCase(id) {
      const started = Date.now();
      let record = await showCase(id);
      while (PENDING.includes(record.status)) {
        const seconds = Math.round((Date.now() - started) / 1000);
        if (seconds >= FOLLOW_LIMIT_SECONDS) {
          noteStatus(record, "no result after " + seconds + " s. Check the worker log, then reload to keep watching.");
          return record;
        }
        noteStatus(record, record.status === "queued"
          ? "waiting for a worker (" + seconds + " s)"
          : "a worker is analysing it (" + seconds + " s)");
        await new Promise((resolve) => setTimeout(resolve, seconds < 15 ? 1000 : 3000));
        record = await showCase(id);
      }
      return record;
    }

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

    const UNANSWERED_REASONS = {
      no_relevant_source: "No policy in force on that date matched the question.",
      model_reported_insufficient_sources: "The model said these sources do not answer the question.",
      malformed_model_output: "The model did not return a usable answer.",
    };
    const FAILED_REASONS = {
      model_answer_failed: "The model could not be reached. Check that the model server is running.",
      question_attempts_exhausted: "The worker stopped before it could answer.",
    };

    function sourceLabel(source) {
      return "[" + source.source + "] " + source.name + " v" + source.version + " " + source.section_ref +
        (source.citation ? " · " + source.citation : "") +
        (source.effective_from ? " · effective " + source.effective_from : "");
    }

    function sourceLink(source) {
      const block = document.createElement("p");
      block.className = "muted";
      if (source.source_url && /^https:\\/\\//.test(source.source_url)) {
        const link = document.createElement("a");
        link.href = source.source_url;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = sourceLabel(source);
        block.append(link);
      } else {
        block.textContent = sourceLabel(source);
      }
      return block;
    }

    function renderQuestion(record, note) {
      const view = document.querySelector("#question-view");
      view.innerHTML = "";
      const status = document.createElement("p");
      status.className = "muted";
      status.textContent = "Status: " + record.status + (note ? " · " + note : "") +
        (record.retrieval_mode ? " · retrieval " + record.retrieval_mode : "") +
        (record.model_name ? " · " + record.model_name : "") +
        (record.latency_ms ? " · " + (record.latency_ms / 1000).toFixed(1) + " s" : "");
      view.append(status);
      const byNumber = new Map((record.sources || []).map((source) => [source.source, source]));
      if (record.status === "answered") {
        const answer = document.createElement("p");
        answer.className = "answer";
        answer.textContent = record.answer;
        view.append(answer);
        for (const citation of record.citations) {
          const block = document.createElement("div");
          block.className = "quote";
          const quote = document.createElement("p");
          quote.textContent = "“" + citation.quote + "”";
          block.append(quote);
          const source = byNumber.get(citation.source);
          if (source) block.append(sourceLink(source));
          view.append(block);
        }
      } else if (record.status === "unanswered") {
        const reasons = new Set((record.validation_errors || []).map((code) =>
          UNANSWERED_REASONS[code.split(":")[0]] ||
          "The model's answer did not quote the sources accurately, so it is not shown."));
        const message = document.createElement("p");
        message.className = "answer";
        message.textContent = "No supported answer. " + Array.from(reasons).join(" ");
        view.append(message);
      } else if (record.status === "failed") {
        const message = document.createElement("p");
        message.className = "error";
        message.textContent = FAILED_REASONS[record.error_code] || "The question could not be answered.";
        view.append(message);
      }
      if ((record.sources || []).length) {
        const details = document.createElement("details");
        details.open = record.status === "unanswered";
        const summary = document.createElement("summary");
        summary.textContent = "Sources the model was given (" + record.sources.length + ")";
        details.append(summary);
        for (const source of record.sources) {
          const block = sourceLink(source);
          if (source.heading) block.append(" · " + source.heading);
          details.append(block);
        }
        view.append(details);
      }
      if ((record.injection_signals || []).length) {
        const warning = document.createElement("p");
        warning.className = "error";
        warning.textContent = "The question contained instruction-like text; read the answer with care.";
        view.append(warning);
      }
    }

    const PENDING_QUESTION = ["queued", "answering"];

    async function followQuestion(id) {
      const started = Date.now();
      let record = await readJson(await fetch("/api/v1/questions/" + id));
      while (PENDING_QUESTION.includes(record.status)) {
        const seconds = Math.round((Date.now() - started) / 1000);
        if (seconds >= FOLLOW_LIMIT_SECONDS) {
          renderQuestion(record, "no result after " + seconds + " s. Check the worker log, then reload.");
          return record;
        }
        renderQuestion(record, record.status === "queued"
          ? "waiting for a worker (" + seconds + " s)"
          : "the model is answering (" + seconds + " s)");
        await new Promise((resolve) => setTimeout(resolve, seconds < 15 ? 1000 : 3000));
        record = await readJson(await fetch("/api/v1/questions/" + id));
      }
      renderQuestion(record);
      return record;
    }

    document.querySelector("#question-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.target.querySelector("button");
      const view = document.querySelector("#question-view");
      button.disabled = true;
      view.textContent = "Submitting…";
      try {
        const created = await readJson(await fetch("/api/v1/questions", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            question: document.querySelector("#question").value,
            as_of: document.querySelector("#question-as-of").value,
          }),
        }));
        history.replaceState(null, "", "#question=" + created.id);
        await followQuestion(created.id);
      } catch (error) {
        view.innerHTML = "<p class='error'></p>";
        view.querySelector("p").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });

    const linked = location.hash.match(/^#(case|question)=([0-9a-f-]{36})$/i);
    if (linked && linked[1] === "case") {
      caseId = linked[2];
      followCase(caseId).catch((error) => {
        document.querySelector("#case-view").textContent = error.message;
      });
    } else if (linked) {
      followQuestion(linked[2]).catch((error) => {
        document.querySelector("#question-view").textContent = error.message;
      });
    }
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
