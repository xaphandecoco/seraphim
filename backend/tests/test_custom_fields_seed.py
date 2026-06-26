"""Seed idempotency and content tests for custom field groups (S02 §8 block 3).

The seed function takes a *sync* SQLAlchemy Connection. In the async test
environment we use ``conn.run_sync(seed_church_custom_fields)`` to execute
it on the underlying sync connection, matching the Alembic migration pattern.
"""
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CustomFieldDef, CustomFieldGroup
from app.seeds.custom_fields_seed import seed_church_custom_fields


async def _run_seed(db_session: AsyncSession) -> None:
    """Run the seed inside the test's async session using run_sync."""
    await db_session.run_sync(seed_church_custom_fields)
    # run_sync may issue a commit inside the sync context; refresh ORM state
    await db_session.commit()


async def test_seed_creates_six_contact_groups(db_session: AsyncSession):
    """Seed creates exactly 6 groups with entity='contact'."""
    await _run_seed(db_session)

    result = await db_session.execute(
        select(CustomFieldGroup).where(CustomFieldGroup.entity == "contact")
    )
    groups = result.scalars().all()
    assert len(groups) == 6

    expected_names = {
        "constituent_info",
        "new_friend_info",
        "community_info",
        "leader_info",
        "followup_info",
        "church_info",
    }
    actual_names = {g.name for g in groups}
    assert actual_names == expected_names


async def test_seed_weight_ordering(db_session: AsyncSession):
    """Groups are at weights 10, 20, 30, 40, 50, 60."""
    await _run_seed(db_session)

    result = await db_session.execute(
        select(CustomFieldGroup)
        .where(CustomFieldGroup.entity == "contact")
        .order_by(CustomFieldGroup.weight)
    )
    groups = list(result.scalars().all())
    weights = [g.weight for g in groups]
    assert weights == [10, 20, 30, 40, 50, 60]


async def test_seed_idempotent(db_session: AsyncSession):
    """Running the seed twice produces the same 6 groups and no duplicate rows."""
    await _run_seed(db_session)
    await _run_seed(db_session)

    result = await db_session.execute(
        select(CustomFieldGroup).where(CustomFieldGroup.entity == "contact")
    )
    groups = result.scalars().all()
    assert len(groups) == 6

    # Field count should also be unchanged (no duplicates)
    result2 = await db_session.execute(
        select(CustomFieldDef)
        .join(CustomFieldGroup, CustomFieldDef.group_id == CustomFieldGroup.id)
        .where(CustomFieldGroup.entity == "contact")
    )
    all_fields = result2.scalars().all()
    # Names should be unique (no duplicates per group)
    field_keys = [(f.group_id, f.name) for f in all_fields]
    assert len(field_keys) == len(set(field_keys))


async def test_seed_preserves_admin_edits(db_session: AsyncSession):
    """Running seed again does NOT overwrite existing group labels (insert-if-missing)."""
    await _run_seed(db_session)

    # Modify a group label as if an admin edited it
    result = await db_session.execute(
        select(CustomFieldGroup).where(
            CustomFieldGroup.name == "constituent_info",
            CustomFieldGroup.entity == "contact",
        )
    )
    group = result.scalar_one()
    group.label = "Admin Edited Label"
    db_session.add(group)
    await db_session.commit()

    # Re-run seed -> label should be unchanged
    await _run_seed(db_session)

    await db_session.refresh(group)
    assert group.label == "Admin Edited Label"


async def test_seed_includes_contact_reference_fields(db_session: AsyncSession):
    """invited_by, consolidated_by, community_leader are contact_reference with is_multi=False."""
    await _run_seed(db_session)

    result = await db_session.execute(
        select(CustomFieldDef)
        .where(CustomFieldDef.data_type == "contact_reference")
    )
    ref_fields = result.scalars().all()
    ref_names = {f.name for f in ref_fields}

    expected_ref_fields = {"invited_by", "consolidated_by", "community_leader"}
    assert expected_ref_fields.issubset(ref_names)

    # All single-link (is_multi=False) for the core ref fields
    for field in ref_fields:
        if field.name in expected_ref_fields:
            assert field.is_multi is False, (
                f"Expected {field.name} to be single-link (is_multi=False)"
            )


async def test_seed_includes_multiselect_fields(db_session: AsyncSession):
    """community and ministry are multiselect with is_multi=True."""
    await _run_seed(db_session)

    result = await db_session.execute(
        select(CustomFieldDef).where(CustomFieldDef.data_type == "multiselect")
    )
    multi_fields = result.scalars().all()
    multi_names = {f.name for f in multi_fields}

    assert "community" in multi_names, "community field should be multiselect"
    assert "ministry" in multi_names, "ministry field should be multiselect"

    for field in multi_fields:
        if field.name in ("community", "ministry"):
            assert field.is_multi is True, (
                f"Expected {field.name} to have is_multi=True"
            )
