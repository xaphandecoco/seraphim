"""Public router — unauthenticated newcomer intake endpoints (S13).

Prefix : /public
Auth   : NONE — registered in main.py without check_setup_complete or any auth dep.

ASSUMPTION (CORS): Cross-origin embedding on a different domain is deferred.
Deployment is same-origin (Cloudflare Tunnel -> nginx -> same container), so the
existing app-level CORSMiddleware is sufficient for the public newcomer form.
If the form is ever embedded on a separate church-website domain, add
admin_settings key 'newcomer_allowed_origins' and update this router to set
appropriate response headers.  See docs/BLOCKERS.md S13 entry.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.rate_limit import limiter
from app.schemas import NewcomerResult, NewcomerSubmission, PublicProfileSchema
from app.services.profile_service import get_public_profile_schema, process_newcomer_submission

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/newcomer/profile", response_model=PublicProfileSchema)
@limiter.limit("30/minute")
async def get_newcomer_profile(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PublicProfileSchema:
    """Return the public newcomer form schema (no auth required).

    Rate-limited: 30/minute per IP (L3 — prevents unauthenticated hammering).
    request must be the FIRST parameter so slowapi can read the client IP.
    Returns 404 when no profile has is_public=True.
    """
    schema = await get_public_profile_schema(db)
    if schema is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No public profile configured",
        )
    return schema


@router.post("/newcomer", response_model=NewcomerResult, status_code=201)
@limiter.limit("5/minute;20/hour")
async def submit_newcomer(
    request: Request,
    submission: NewcomerSubmission,
    db: AsyncSession = Depends(get_db),
) -> NewcomerResult:
    """Public newcomer intake endpoint.

    Rate-limited: 5/minute, 20/hour per IP (slowapi in-memory for tests; Redis in prod).
    Honeypot: if the hidden 'website' field is filled, silently accept but create nothing.
    request must be the FIRST parameter so slowapi can read the client IP.
    """
    # Honeypot guard — bots that fill hidden fields get a silent 201 with no side-effects
    if submission.website:
        return NewcomerResult(
            contact_id=0,
            status="created",
            message="Thank you for your submission.",
        )

    return await process_newcomer_submission(submission, db)
