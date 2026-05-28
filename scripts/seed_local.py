#!/usr/bin/env python3
"""Seed script for local development. Creates test data."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, engine
from app.models import (
    Camera,
    CiviCRMEvent,
    CiviCRMMember,
    User,
)
from app.utils.auth import hash_password


async def seed():
    async with async_session() as session:
        # Check if already seeded
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none():
            print("Database already seeded. Skipping.")
            return

        # Create admin user
        admin = User(
            email="admin@lightnc.org",
            password_hash=hash_password("admin"),
            auth_provider="local",
            role="admin",
            is_active=True,
        )
        session.add(admin)

        # Create volunteer users
        volunteers = [
            User(
                email="volunteer1@lightnc.org",
                password_hash=hash_password("volunteer1"),
                auth_provider="local",
                role="volunteer",
                is_active=True,
            ),
            User(
                email="volunteer2@lightnc.org",
                password_hash=hash_password("volunteer2"),
                auth_provider="local",
                role="volunteer",
                is_active=True,
            ),
            User(
                email="volunteer3@lightnc.org",
                password_hash=hash_password("volunteer3"),
                auth_provider="local",
                role="volunteer",
                is_active=True,
            ),
        ]
        session.add_all(volunteers)

        # Create mock CiviCRM members
        members = [
            CiviCRMMember(contact_id=1, first_name="John", last_name="Doe", email="john@example.com"),
            CiviCRMMember(contact_id=2, first_name="Jane", last_name="Smith", email="jane@example.com"),
            CiviCRMMember(contact_id=3, first_name="Robert", last_name="Johnson", email="robert@example.com"),
            CiviCRMMember(contact_id=4, first_name="Maria", last_name="Garcia", email="maria@example.com"),
            CiviCRMMember(contact_id=5, first_name="David", last_name="Lee", email="david@example.com"),
        ]
        session.add_all(members)

        # Create cameras
        cameras = [
            Camera(
                name="Entrance Camera",
                rtsp_url="rtsp://mock-camera-1:8554/cam1",
                zone_label="Main Entrance",
                fps=1,
                enable_health_check=True,
                status="streaming",
            ),
            Camera(
                name="Sanctuary Camera",
                rtsp_url="rtsp://admin:admin@192.168.1.100/stream1",
                zone_label="Sanctuary",
                fps=1,
                enable_health_check=True,
                status="offline",
            ),
        ]
        session.add_all(cameras)

        # Create event
        from datetime import datetime, timezone, timedelta
        event = CiviCRMEvent(
            event_id=1,
            title="Sunday Service",
            start_date=datetime.now(timezone.utc) + timedelta(days=1),
            end_date=datetime.now(timezone.utc) + timedelta(days=1, hours=3),
        )
        session.add(event)

        await session.commit()
        print("Database seeded successfully!")
        print("Admin: admin@lightnc.org / admin")
        print("Volunteers: volunteer1@lightnc.org / volunteer1, etc.")


if __name__ == "__main__":
    asyncio.run(seed())
