from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
from app.models import (
    Attendance,
    Detection,
    Log,
    PitQueue,
    Task,
    TaskAction,
    User,
    VolunteerStat,
)


class TaskService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_next_task(self, volunteer_id: int) -> Optional[Task]:
        """Get next pending task for volunteer, excluding:
        - Tasks they already skipped
        - Tasks they were first confirmer on
        - Expired tasks
        - Tasks in pit
        """
        now = datetime.now(timezone.utc)
        
        # Find tasks this volunteer has already acted on
        acted_subq = select(TaskAction.task_id).where(
            TaskAction.volunteer_id == volunteer_id
        )

        result = await self.session.execute(
            select(Task)
            .join(Detection, Task.detection_id == Detection.id)
            .where(Task.status == "pending")
            .where(Task.expiry_date > now)
            .where(Task.pit_status.is_(None))
            .where(Task.id.notin_(acted_subq))
            .order_by(Detection.timestamp)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def confirm_task(
        self, task_id: int, volunteer_id: int
    ) -> Task:
        """Handle confirm action with dual approval logic."""
        task = await self.session.get(Task, task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        if task.status not in ("pending", "skipped"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task is no longer pending",
            )

        # Check if volunteer already acted on this task
        existing = await self.session.execute(
            select(TaskAction).where(
                (TaskAction.task_id == task_id)
                & (TaskAction.volunteer_id == volunteer_id)
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have already acted on this task",
            )

        # Record action
        action = TaskAction(
            task_id=task_id,
            volunteer_id=volunteer_id,
            action="confirm",
        )
        self.session.add(action)

        task.current_approvals += 1

        if task.current_approvals >= task.required_approvals:
            task.status = "resolved"
            await self._log_attendance(task)
            await self._update_volunteer_stats(volunteer_id, "confirm")
        else:
            task.status = "pending"

        await self.session.commit()
        return task

    async def edit_task(
        self,
        task_id: int,
        volunteer_id: int,
        member_id: int,
    ) -> Task:
        """Handle edit action — change assigned member."""
        task = await self.session.get(Task, task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        if task.status not in ("pending", "skipped"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task is no longer pending",
            )

        existing = await self.session.execute(
            select(TaskAction).where(
                (TaskAction.task_id == task_id)
                & (TaskAction.volunteer_id == volunteer_id)
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have already acted on this task",
            )

        # Record action
        action = TaskAction(
            task_id=task_id,
            volunteer_id=volunteer_id,
            action="edit",
        )
        self.session.add(action)

        # Update detection with new member
        detection = await self.session.get(Detection, task.detection_id)
        if detection:
            detection.matched_name = f"member:{member_id}"

        task.current_approvals += 1

        if task.current_approvals >= task.required_approvals:
            task.status = "resolved"
            await self._log_attendance(task)
            await self._update_volunteer_stats(volunteer_id, "edit")
        else:
            task.status = "pending"

        await self.session.commit()
        return task

    async def add_task(
        self,
        task_id: int,
        volunteer_id: int,
        member_id: int,
    ) -> Task:
        """Handle add action — assign unidentified face to member."""
        task = await self.session.get(Task, task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        if task.status not in ("pending", "skipped"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task is no longer pending",
            )

        existing = await self.session.execute(
            select(TaskAction).where(
                (TaskAction.task_id == task_id)
                & (TaskAction.volunteer_id == volunteer_id)
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have already acted on this task",
            )

        # Record action
        action = TaskAction(
            task_id=task_id,
            volunteer_id=volunteer_id,
            action="add",
        )
        self.session.add(action)

        # Update detection
        detection = await self.session.get(Detection, task.detection_id)
        if detection:
            detection.matched_name = f"member:{member_id}"
            detection.is_enrolled = True

        task.current_approvals += 1

        if task.current_approvals >= task.required_approvals:
            task.status = "resolved"
            await self._log_attendance(task)
            await self._update_volunteer_stats(volunteer_id, "add")
        else:
            task.status = "pending"

        await self.session.commit()
        return task

    async def skip_task(
        self, task_id: int, volunteer_id: int, reason: Optional[str] = None
    ) -> Task:
        """Handle skip action — record skip and return to queue."""
        task = await self.session.get(Task, task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        # Check skip count per volunteer
        existing_skips = await self.session.execute(
            select(func.count(TaskAction.id)).where(
                (TaskAction.task_id == task_id)
                & (TaskAction.volunteer_id == volunteer_id)
                & (TaskAction.action == "skip")
            )
        )
        skip_count_by_volunteer = existing_skips.scalar() or 0
        if skip_count_by_volunteer >= 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have already skipped this task twice",
            )

        # Record action
        action = TaskAction(
            task_id=task_id,
            volunteer_id=volunteer_id,
            action="skip",
            reason=reason,
        )
        self.session.add(action)

        task.skip_count += 1
        if reason:
            task.skip_reasons = [*(task.skip_reasons or []), reason]

        # Check for pit queue (4 skips from >=2 volunteers)
        if task.skip_count >= 4:
            # Count unique volunteers who skipped
            unique_skippers = await self.session.execute(
                select(func.count(func.distinct(TaskAction.volunteer_id))).where(
                    (TaskAction.task_id == task_id) & (TaskAction.action == "skip")
                )
            )
            unique_count = unique_skippers.scalar() or 0
            if unique_count >= 2:
                task.status = "pit"
                task.pit_status = "awaiting"
                pit = PitQueue(task_id=task_id)
                self.session.add(pit)
            else:
                task.status = "pending"
        else:
            task.status = "pending"

        await self.session.commit()
        return task

    async def admin_override(self, task_id: int, admin_id: int) -> Task:
        """Admin one-click resolve on dual-approval tasks."""
        task = await self.session.get(Task, task_id)
        if not task:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )

        action = TaskAction(
            task_id=task_id,
            volunteer_id=admin_id,
            action="admin_override",
        )
        self.session.add(action)

        task.status = "resolved"
        await self._log_attendance(task)
        await self.session.commit()
        return task

    async def get_task_count(self) -> int:
        """Get total count of pending tasks."""
        result = await self.session.execute(
            select(func.count(Task.id)).where(Task.status == "pending")
        )
        return result.scalar() or 0

    async def _log_attendance(self, task: Task):
        """Create attendance record when task is resolved."""
        detection = await self.session.get(Detection, task.detection_id)
        if not detection or not detection.event_id:
            return

        # Check for duplicate
        existing = await self.session.execute(
            select(Attendance).where(
                (Attendance.detection_id == task.detection_id)
                & (Attendance.event_id == detection.event_id)
            )
        )
        if existing.scalar_one_or_none():
            return

        # Extract member info from detection
        member_id = None
        if detection.matched_name and detection.matched_name.startswith("member:"):
            try:
                member_id = int(detection.matched_name.split(":")[1])
            except (ValueError, IndexError):
                pass

        record = Attendance(
            contact_id=member_id,
            event_id=detection.event_id,
            detection_id=task.detection_id,
            status="confirmed",
            push_status="pending",
        )
        self.session.add(record)

        # Log
        log = Log(
            detection_id=task.detection_id,
            timestamp=detection.timestamp,
            camera_id=detection.camera_id,
            matched_name=detection.matched_name,
            confidence=detection.confidence,
            tier=detection.tier,
            action="confirmed",
            event_id=detection.event_id,
        )
        self.session.add(log)

    async def _update_volunteer_stats(self, volunteer_id: int, action_type: str):
        """Update volunteer gamification stats."""
        now = datetime.now(timezone.utc)
        month_key = now.strftime("%Y-%m")

        stat = await self.session.get(VolunteerStat, (volunteer_id, month_key))
        if not stat:
            stat = VolunteerStat(volunteer_id=volunteer_id, month=month_key)
            self.session.add(stat)

        points = 2 if action_type == "edit" else 1
        if action_type == "confirm":
            stat.tasks_confirmed += 1
        elif action_type == "edit":
            stat.tasks_edited += 1
        elif action_type == "add":
            stat.tasks_added += 1

        stat.total_points += points
