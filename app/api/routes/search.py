from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.domain.schemas import SearchHit, SearchResponse
from app.infrastructure.auth import AuthContext, require_roles
from app.infrastructure.database import get_session
from app.services.classifier import load_cfr_prior
from app.services.embeddings import (
    Embedder,
    RetrievalUnavailable,
    read_embedding_index,
    sentence_transformer_embedder,
)
from app.services.retrieval import RetrievalMode, resolve_retrieval_mode, retrieve_policies

router = APIRouter(prefix="/api/v1", tags=["search"])
ViewerContext = Annotated[
    AuthContext,
    Depends(require_roles("admin", "analyst", "reviewer", "viewer")),
]
DatabaseSession = Annotated[Session, Depends(get_session)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]


def get_embedder() -> Embedder | None:
    """Keyword search does not need a model. Tests override this for hybrid queries."""
    return None


@router.get("/search", response_model=SearchResponse)
def search_sections(
    _context: ViewerContext,
    session: DatabaseSession,
    settings: ApplicationSettings,
    q: Annotated[str, Query(min_length=1, max_length=4000)],
    as_of: Annotated[date | None, Query()] = None,
    mode: Annotated[RetrievalMode | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    embedder: Annotated[Embedder | None, Depends(get_embedder)] = None,
) -> SearchResponse:
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"
    requested = mode if mode is not None else settings.retrieval_mode
    selected: RetrievalMode = resolve_retrieval_mode(requested, dialect)
    case_date = as_of or date.today()
    index = None
    active_embedder = embedder
    try:
        if selected in {"embedding", "hybrid"} and active_embedder is None:
            index_path = Path(settings.embedding_index_path)
            if index_path.is_file():
                _model_name, index = read_embedding_index(index_path)
            active_embedder = sentence_transformer_embedder(settings.embedding_model)
        found = retrieve_policies(
            session,
            q,
            case_date,
            limit,
            mode=selected,
            embedder=active_embedder,
            embedding_index=index,
            cfr_prior=load_cfr_prior(Path(settings.cfr_prior_path)),
        )
    except RetrievalUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SearchResponse(
        mode=selected,
        as_of=case_date,
        results=[
            SearchHit(
                policy_id=item.id,
                name=item.name,
                version=item.version,
                section_ref=item.section_ref,
                heading=item.heading,
                content=item.content,
                score=item.score,
                lexical_score=item.lexical_score,
                embedding_score=item.embedding_score,
            )
            for item in found
        ],
    )
