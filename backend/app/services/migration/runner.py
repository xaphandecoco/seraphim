"""Phase orchestrator for the CiviCRM migration pipeline.

Public API
----------
async def run_phase(
    phase: str,
    xlsx_path: str,
    column_map: dict,
    options: dict,
    dry_run: bool,
    created_by_id: int | None,
    db_factory,
) -> int

    Runs one import phase ('contacts', 'events', 'participants', 'links')
    end-to-end and returns the ImportBatch.id.

Phases
------
contacts      Upsert Contact rows from the XLSX.  Preflight validates every
              custom_data.* target against CustomFieldDef.
events        Upsert Event rows from the XLSX.
participants  Pre-load contact/event id maps then bulk-upsert Participant rows.
links         For each row, resolve contact_external_id -> contact_id, then for
              each custom_data field in the row call match_name and either write
              the link (SINGLE) or record a review outcome (AMBIGUOUS/UNMATCHED).

Chunk sizes
-----------
contacts / events : 500 rows per flush
participants      : 1000 rows per flush
links             : 1 (each row is committed individually after match_name)

dry_run
-------
When True, core table writes (Contact, Event, Participant, Contact.custom_data)
are wrapped in a savepoint (``begin_nested``) that is rolled back after each
chunk, while ImportBatch + ImportRowResult rows are committed on a separate
connection path so the batch remains UI-inspectable.

CN-24 (member_status_service recompute)
---------------------------------------
After a successful LIVE run, this module attempts to import and call
``member_status_service.recompute_all_contacts(db)``; if the module is absent
(ImportError) the step is silently skipped.

Circular-import rule (CN-25): this module MUST NOT import from any router.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Contact,
    CustomFieldDef,
    Event,
    ImportBatch,
    ImportRowResult,
    utc_now,
)
from app.services.migration.loader import (
    bulk_insert_participants,
    upsert_contact,
    upsert_event,
    write_people_link,
)
from app.services.migration.mapper import (
    BatchFatalError,
    map_contact_row,
    map_event_row,
    map_participant_row,
    validate_column_map,
)
from app.services.migration.reader import read_rows
from app.services.name_match import match_name

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chunk sizes
# ---------------------------------------------------------------------------

_CHUNK_CONTACTS = 500
_CHUNK_EVENTS = 500
_CHUNK_PARTICIPANTS = 1000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _load_custom_field_names(db: AsyncSession) -> frozenset[str]:
    """Return the set of active CustomFieldDef.name values for contacts."""
    result = await db.execute(
        select(CustomFieldDef.name).where(CustomFieldDef.is_active.is_(True))
    )
    return frozenset(row[0] for row in result.all())


async def _preflight_contacts(
    column_map: dict[str, Any],
    db: AsyncSession,
) -> None:
    """Validate every custom_data.* target exists as an active CustomFieldDef.

    Raises BatchFatalError listing all unknown field names if any are missing.
    validate_column_map() already ensures all non-custom_data targets are valid
    Contact fields; this step is exclusively for the custom_data.* namespace.
    """
    known_fields = await _load_custom_field_names(db)
    unknown: list[str] = []
    for header, spec in column_map.items():
        target: str = spec.get("target", "")
        if target.startswith("custom_data."):
            field_name = target[len("custom_data."):]
            if field_name and field_name not in known_fields:
                unknown.append(f"{header!r} -> {target!r} (field {field_name!r} not in CustomFieldDef)")

    if unknown:
        raise BatchFatalError(
            "Preflight failed — unknown custom_data target(s): " + ", ".join(unknown)
        )


async def _preload_contact_id_map(db: AsyncSession) -> dict[str, int]:
    """Return {str(external_id): contact_id} for all contacts with an external_id."""
    result = await db.execute(
        select(Contact.external_id, Contact.id).where(Contact.external_id.isnot(None))
    )
    return {str(row[0]): row[1] for row in result.all()}


async def _preload_event_id_map(db: AsyncSession) -> dict[str, int]:
    """Return {str(external_id): event_id} for all events with an external_id."""
    result = await db.execute(
        select(Event.external_id, Event.id).where(Event.external_id.isnot(None))
    )
    return {str(row[0]): row[1] for row in result.all()}


def _tally_outcomes(results: list[ImportRowResult]) -> dict[str, int]:
    """Count outcomes from a list of ImportRowResult objects."""
    counts: dict[str, int] = {
        "total_rows": len(results),
        "created_count": 0,
        "updated_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "review_count": 0,
    }
    for r in results:
        key = f"{r.outcome}_count"
        if key in counts:
            counts[key] += 1
    return counts


# ---------------------------------------------------------------------------
# Phase runners (internal)
# ---------------------------------------------------------------------------


async def _run_contacts_phase(
    batch: ImportBatch,
    xlsx_path: str,
    column_map: dict[str, Any],
    options: dict[str, Any],
    dry_run: bool,
    db: AsyncSession,
) -> list[ImportRowResult]:
    """Read + upsert contacts; return list of ImportRowResult (not yet flushed)."""
    row_results: list[ImportRowResult] = []
    buffer: list[tuple[int, dict, Any]] = []  # (row_number, row, mapped_or_error)
    row_number = 0

    async def _flush_buffer(buf: list[tuple[int, dict, Any]]) -> None:
        if not buf:
            return
        # Build audit rows while running the (savepoint-wrapped) core upserts, but
        # only register them with the session AFTER the savepoint resolves. A
        # dry-run rolls back the savepoint to discard the core Contact writes — if
        # the ImportRowResult rows were added inside it they would be discarded
        # too, leaving an empty per-row report (AC 13 regression).
        pending: list[ImportRowResult] = []
        if dry_run:
            sp = await db.begin_nested()
        try:
            for rn, raw_row, mapped in buf:
                if isinstance(mapped, Exception) or hasattr(mapped, "message"):
                    # MappingError
                    pending.append(ImportRowResult(
                        batch_id=batch.id,
                        row_number=rn,
                        external_id=str(raw_row.get("external_id", "")),
                        outcome="error",
                        entity_id=None,
                        message=getattr(mapped, "message", str(mapped)),
                        raw=None,
                    ))
                    continue

                outcome, entity_id, message = await upsert_contact(
                    db, dict(vars(mapped)), dry_run=dry_run
                )
                pending.append(ImportRowResult(
                    batch_id=batch.id,
                    row_number=rn,
                    external_id=str(getattr(mapped, "external_id", "") or ""),
                    outcome=outcome,
                    entity_id=entity_id,
                    message=message,
                    raw=None,
                ))

            if dry_run:
                await sp.rollback()
            else:
                await db.flush()
        except Exception:
            if dry_run:
                try:
                    await sp.rollback()
                except Exception:
                    pass
            raise

        # Persist audit rows after the savepoint resolves (survives dry-run rollback).
        for irr in pending:
            row_results.append(irr)
            db.add(irr)

    async for raw_row in read_rows(xlsx_path):
        row_number += 1
        mapped = await map_contact_row(raw_row, column_map, options)
        buffer.append((row_number, raw_row, mapped))

        if len(buffer) >= _CHUNK_CONTACTS:
            await _flush_buffer(buffer)
            await db.commit()
            buffer = []

    if buffer:
        await _flush_buffer(buffer)
        await db.commit()

    return row_results


async def _run_events_phase(
    batch: ImportBatch,
    xlsx_path: str,
    column_map: dict[str, Any],
    options: dict[str, Any],
    dry_run: bool,
    db: AsyncSession,
) -> list[ImportRowResult]:
    """Read + upsert events; return list of ImportRowResult."""
    row_results: list[ImportRowResult] = []
    buffer: list[tuple[int, dict, Any]] = []
    row_number = 0

    async def _flush_buffer(buf: list[tuple[int, dict, Any]]) -> None:
        if not buf:
            return
        # See contacts phase: build audit rows here but register them after the
        # savepoint resolves so a dry-run rollback keeps the import_row_result rows.
        pending: list[ImportRowResult] = []
        if dry_run:
            sp = await db.begin_nested()
        try:
            for rn, raw_row, mapped in buf:
                if hasattr(mapped, "message"):
                    pending.append(ImportRowResult(
                        batch_id=batch.id,
                        row_number=rn,
                        external_id=str(raw_row.get("external_id", "")),
                        outcome="error",
                        entity_id=None,
                        message=mapped.message,
                        raw=None,
                    ))
                    continue

                outcome, entity_id, message = await upsert_event(
                    db, dict(vars(mapped)), dry_run=dry_run
                )
                pending.append(ImportRowResult(
                    batch_id=batch.id,
                    row_number=rn,
                    external_id=str(getattr(mapped, "external_id", "") or ""),
                    outcome=outcome,
                    entity_id=entity_id,
                    message=message,
                    raw=None,
                ))

            if dry_run:
                await sp.rollback()
            else:
                await db.flush()
        except Exception:
            if dry_run:
                try:
                    await sp.rollback()
                except Exception:
                    pass
            raise

        # Persist audit rows after the savepoint resolves (survives dry-run rollback).
        for irr in pending:
            row_results.append(irr)
            db.add(irr)

    async for raw_row in read_rows(xlsx_path):
        row_number += 1
        mapped = await map_event_row(raw_row, column_map, options)
        buffer.append((row_number, raw_row, mapped))

        if len(buffer) >= _CHUNK_EVENTS:
            await _flush_buffer(buffer)
            await db.commit()
            buffer = []

    if buffer:
        await _flush_buffer(buffer)
        await db.commit()

    return row_results


async def _run_participants_phase(
    batch: ImportBatch,
    xlsx_path: str,
    column_map: dict[str, Any],
    options: dict[str, Any],
    dry_run: bool,
    db: AsyncSession,
) -> list[ImportRowResult]:
    """Pre-load id maps, read + bulk-upsert participants."""
    contact_id_map = await _preload_contact_id_map(db)
    event_id_map = await _preload_event_id_map(db)

    row_results: list[ImportRowResult] = []
    buffer_mapped: list[Any] = []
    buffer_raws: list[tuple[int, dict]] = []
    row_number = 0

    async def _flush_buffer(
        mapped_buf: list[Any], raws_buf: list[tuple[int, dict]]
    ) -> None:
        if not mapped_buf:
            return

        # Separate errors from valid rows
        error_rows: list[tuple[int, dict, Any]] = []
        valid_mapped: list[Any] = []
        valid_raws: list[tuple[int, dict]] = []
        for (rn, raw_row), mapped in zip(raws_buf, mapped_buf):
            if hasattr(mapped, "message"):
                error_rows.append((rn, raw_row, mapped))
            else:
                valid_mapped.append(mapped)
                valid_raws.append((rn, raw_row))

        # Emit errors immediately
        for rn, raw_row, mapped in error_rows:
            irr = ImportRowResult(
                batch_id=batch.id,
                row_number=rn,
                external_id=str(raw_row.get("contact_ref", "")),
                outcome="error",
                entity_id=None,
                message=mapped.message,
                raw=None,
            )
            row_results.append(irr)
            db.add(irr)

        if not valid_mapped:
            await db.flush()
            return

        # Build participant dicts for bulk_insert
        participant_dicts = [
            {
                "contact_external_id": m.contact_ref,
                "event_external_id": m.event_ref,
                "status": m.status,
                "role": m.role,
                "source": m.source,
            }
            for m in valid_mapped
        ]

        # See contacts phase: defer audit-row registration until after the
        # savepoint resolves so a dry-run rollback keeps the import_row_result rows.
        pending: list[ImportRowResult] = []
        if dry_run:
            sp = await db.begin_nested()
        try:
            loader_results = await bulk_insert_participants(
                db, participant_dicts, contact_id_map, event_id_map, dry_run=dry_run
            )
            for (rn, raw_row), (outcome, entity_id, message) in zip(valid_raws, loader_results):
                pending.append(ImportRowResult(
                    batch_id=batch.id,
                    row_number=rn,
                    external_id=str(raw_row.get("contact_ref", "")),
                    outcome=outcome,
                    entity_id=entity_id,
                    message=message,
                    raw=None,
                ))

            if dry_run:
                await sp.rollback()
            else:
                await db.flush()
        except Exception:
            if dry_run:
                try:
                    await sp.rollback()
                except Exception:
                    pass
            raise

        # Persist audit rows after the savepoint resolves (survives dry-run rollback).
        for irr in pending:
            row_results.append(irr)
            db.add(irr)

    async for raw_row in read_rows(xlsx_path):
        row_number += 1
        mapped = await map_participant_row(raw_row, column_map, options)
        buffer_mapped.append(mapped)
        buffer_raws.append((row_number, raw_row))

        if len(buffer_mapped) >= _CHUNK_PARTICIPANTS:
            await _flush_buffer(buffer_mapped, buffer_raws)
            await db.commit()
            buffer_mapped = []
            buffer_raws = []

    if buffer_mapped:
        await _flush_buffer(buffer_mapped, buffer_raws)
        await db.commit()

    return row_results


async def _run_links_phase(
    batch: ImportBatch,
    xlsx_path: str,
    column_map: dict[str, Any],
    options: dict[str, Any],
    dry_run: bool,
    db: AsyncSession,
) -> list[ImportRowResult]:
    """For each row, resolve contact and call match_name for custom_data link fields.

    Column map entries with target starting with 'custom_data.' and whose
    source values look like person names are treated as people-link fields.
    The links phase expects:
      - 'contact_external_id' field in the row (to identify the source contact)
      - one or more 'custom_data.<field_name>' targets that contain a person name
    """
    row_results: list[ImportRowResult] = []
    contact_id_map = await _preload_contact_id_map(db)
    row_number = 0

    # Identify which column_map entries are people-link fields
    # target starts with 'custom_data.' and maps to a contact name
    link_fields: list[tuple[str, str]] = []  # (source_header, field_name)
    for header, spec in column_map.items():
        target: str = spec.get("target", "")
        if target.startswith("custom_data."):
            field_name = target[len("custom_data."):]
            if field_name:
                link_fields.append((header, field_name))

    async for raw_row in read_rows(xlsx_path):
        row_number += 1

        # Resolve source contact
        c_ext = str(raw_row.get("contact_external_id") or "")
        contact_app_id = contact_id_map.get(c_ext)
        if contact_app_id is None:
            irr = ImportRowResult(
                batch_id=batch.id,
                row_number=row_number,
                external_id=c_ext,
                outcome="error",
                entity_id=None,
                message=f"contact external_id {c_ext!r} not found",
                raw=None,
            )
            row_results.append(irr)
            db.add(irr)
            await db.commit()
            continue

        row_had_action = False
        for source_header, field_name in link_fields:
            raw_name = raw_row.get(source_header)
            if raw_name is None or str(raw_name).strip() == "":
                continue

            raw_name_str = str(raw_name).strip()

            # Re-read contact's current custom_data to check if field already set
            result = await db.execute(
                select(Contact.custom_data).where(Contact.id == contact_app_id)
            )
            contact_row = result.one_or_none()
            current_cd: dict = {}
            if contact_row is not None:
                current_cd = dict(contact_row[0] or {})

            if field_name in current_cd and current_cd[field_name] is not None:
                # Already set — skip (re-run protection)
                irr = ImportRowResult(
                    batch_id=batch.id,
                    row_number=row_number,
                    external_id=c_ext,
                    outcome="skipped",
                    entity_id=contact_app_id,
                    message=f"field {field_name!r} already set on contact {contact_app_id}",
                    raw=None,
                )
                row_results.append(irr)
                db.add(irr)
                row_had_action = True
                continue

            # Call match_name — it will enqueue review for AMBIGUOUS/UNMATCHED
            match_payload = {
                "batch_id": batch.id,
                "field_name": field_name,
                "source_contact_id": contact_app_id,
            }
            match_result = await match_name(
                raw_name=raw_name_str,
                db=db,
                source="migration",
                payload=match_payload,
                auto_enqueue=True,
            )

            outcome_str = match_result["outcome"]

            if outcome_str == "SINGLE":
                resolved_id = match_result["contact_id"]
                if not dry_run:
                    await write_people_link(db, contact_app_id, field_name, resolved_id)
                    await db.flush()
                irr = ImportRowResult(
                    batch_id=batch.id,
                    row_number=row_number,
                    external_id=c_ext,
                    outcome="created",
                    entity_id=contact_app_id,
                    message=f"linked {field_name!r} -> contact {resolved_id}",
                    raw=None,
                )
            else:
                # AMBIGUOUS or UNMATCHED — match_name already enqueued the review row
                irr = ImportRowResult(
                    batch_id=batch.id,
                    row_number=row_number,
                    external_id=c_ext,
                    outcome="review",
                    entity_id=contact_app_id,
                    message=(
                        f"match_name outcome={outcome_str} for {raw_name_str!r} "
                        f"field={field_name!r} review_queue_id={match_result.get('review_queue_id')}"
                    ),
                    raw=None,
                )

            row_results.append(irr)
            db.add(irr)
            row_had_action = True
            await db.commit()

        if not row_had_action:
            # Row had no actionable link fields — record as skipped
            irr = ImportRowResult(
                batch_id=batch.id,
                row_number=row_number,
                external_id=c_ext,
                outcome="skipped",
                entity_id=contact_app_id,
                message="no link fields to process",
                raw=None,
            )
            row_results.append(irr)
            db.add(irr)
            await db.commit()

    return row_results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_phase(
    phase: str,
    xlsx_path: str,
    column_map: dict[str, Any],
    options: dict[str, Any],
    dry_run: bool,
    created_by_id: Optional[int],
    db_factory,
) -> int:
    """Run one import phase end-to-end and return the ImportBatch.id.

    Parameters
    ----------
    phase:
        One of 'contacts', 'events', 'participants', 'links'.
    xlsx_path:
        Absolute path to the source XLSX file.
    column_map:
        ``{source_header: {target, data_type, ...}}`` mapping.
    options:
        Arbitrary loader options (date_format, default_event_type, etc.).
    dry_run:
        When True, core table writes are wrapped in savepoints that roll back,
        but ImportBatch + ImportRowResult rows are committed so the batch is
        UI-inspectable.
    created_by_id:
        PK of the admin User who triggered this run; may be None.
    db_factory:
        Async session factory (typically ``app.database.async_session``).

    Returns
    -------
    int
        The ImportBatch.id of the created batch record.
    """
    source_filename = os.path.basename(xlsx_path)

    # Map plural phase names to singular entity names (used for both DB storage
    # and validate_column_map which only accepts singular forms).
    _entity_for_phase: dict[str, str] = {
        "contacts": "contact",
        "events": "event",
        "participants": "participant",
        "links": "contact",
    }
    entity = _entity_for_phase.get(phase, phase)

    async with db_factory() as db:
        # ------------------------------------------------------------------
        # 1. Create ImportBatch with status='running' and commit BEFORE rows
        # ------------------------------------------------------------------
        batch = ImportBatch(
            source_filename=source_filename,
            entity=entity,
            # Spec §3.1: mode records the RUN kind (dry_run|live) so the audit
            # trail distinguishes a dry-run preview from a real import — critical
            # during the freeze-window cutover. (Earlier code stored an invented
            # 'upsert' op-mode that erased this distinction.)
            mode="dry_run" if dry_run else "live",
            status="running",
            column_map=column_map,
            options=options,
            created_by_id=created_by_id,
        )
        db.add(batch)
        await db.commit()
        await db.refresh(batch)
        batch_id = batch.id
        logger.info("run_phase: started batch_id=%s phase=%s dry_run=%s", batch_id, phase, dry_run)

        try:
            # ------------------------------------------------------------------
            # 2. Structural preflight (column_map validation)
            # ------------------------------------------------------------------
            validate_column_map(column_map, entity)

            # ------------------------------------------------------------------
            # 3. Phase-specific preflight
            # ------------------------------------------------------------------
            if phase == "contacts":
                try:
                    await _preflight_contacts(column_map, db)
                except BatchFatalError:
                    batch.status = "failed"
                    batch.finished_at = utc_now()
                    await db.commit()
                    raise

            # ------------------------------------------------------------------
            # 4. Run the phase
            # ------------------------------------------------------------------
            if phase == "contacts":
                row_results = await _run_contacts_phase(
                    batch, xlsx_path, column_map, options, dry_run, db
                )
            elif phase == "events":
                row_results = await _run_events_phase(
                    batch, xlsx_path, column_map, options, dry_run, db
                )
            elif phase == "participants":
                row_results = await _run_participants_phase(
                    batch, xlsx_path, column_map, options, dry_run, db
                )
            elif phase == "links":
                row_results = await _run_links_phase(
                    batch, xlsx_path, column_map, options, dry_run, db
                )
            else:
                raise BatchFatalError(f"Unknown phase: {phase!r}")

            # ------------------------------------------------------------------
            # 5. Tally and finalise
            # ------------------------------------------------------------------
            counts = _tally_outcomes(row_results)

            await db.refresh(batch)
            batch.total_rows = counts["total_rows"]
            batch.created_count = counts["created_count"]
            batch.updated_count = counts["updated_count"]
            batch.skipped_count = counts["skipped_count"]
            batch.error_count = counts["error_count"]
            batch.review_count = counts["review_count"]
            batch.status = "completed"
            batch.finished_at = utc_now()
            await db.commit()

            logger.info(
                "run_phase: completed batch_id=%s phase=%s total=%s created=%s "
                "updated=%s skipped=%s errors=%s review=%s dry_run=%s",
                batch_id,
                phase,
                counts["total_rows"],
                counts["created_count"],
                counts["updated_count"],
                counts["skipped_count"],
                counts["error_count"],
                counts["review_count"],
                dry_run,
            )

            # ------------------------------------------------------------------
            # 6. CN-24: recompute member status after a successful live run
            # ------------------------------------------------------------------
            if not dry_run:
                try:
                    from app.services import member_status_service  # noqa: PLC0415

                    await member_status_service.recompute_all_contacts(db)
                    logger.info("run_phase: member_status_service.recompute_all_contacts completed")
                except ImportError:
                    # S23 has not landed yet — skip silently
                    pass

            return batch_id

        except Exception as exc:
            # ------------------------------------------------------------------
            # 7. On any exception: mark batch failed
            # ------------------------------------------------------------------
            logger.exception("run_phase: batch_id=%s failed: %s", batch_id, exc)
            try:
                await db.refresh(batch)
                batch.status = "failed"
                batch.finished_at = utc_now()
                await db.commit()
            except Exception:
                logger.warning(
                    "run_phase: could not persist failed status for batch_id=%s", batch_id
                )
            raise
