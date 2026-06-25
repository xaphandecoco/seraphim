"""Pydantic schemas for the FR-transition verification endpoints (T03).

Kept in a dedicated module to avoid bloating the monolithic schemas.py.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RemapSection(BaseModel):
    """Counts from the ComprefaceSubject remap state."""

    model_config = ConfigDict(from_attributes=True)

    subjects_total: int
    subjects_remapped: int
    subjects_orphaned: int


class ConsentSection(BaseModel):
    """Counts related to BiometricConsent coverage for active subjects."""

    model_config = ConfigDict(from_attributes=True)

    active_subjects_total: int
    consent_rows_created: int
    subjects_missing_consent: int
    enroll_without_consent: bool


class SmokeTestSection(BaseModel):
    """Result of the live-smoke-test gate (never calls CompreFace)."""

    model_config = ConfigDict(from_attributes=True)

    status: str   # 'pass' | 'skip' | 'fail'
    detail: str


class FRTransitionStatus(BaseModel):
    """Top-level response for GET /admin/fr-transition/status."""

    model_config = ConfigDict(from_attributes=True)

    remap: RemapSection
    participants_count: int
    consent: ConsentSection
    smoke_test: SmokeTestSection


class OrphanSubjectRow(BaseModel):
    """Single row returned by GET /admin/fr-transition/orphans."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subject_name: str
    compreface_subject_id: str
    enrollment_status: str


class OrphanSubjectPage(BaseModel):
    """Paginated response for GET /admin/fr-transition/orphans."""

    model_config = ConfigDict(from_attributes=True)

    items: list[OrphanSubjectRow]
    total: int
    page: int
    page_size: int
