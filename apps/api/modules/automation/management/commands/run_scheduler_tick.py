"""One scheduler tick: dispatch outbox events, then drain due jobs.

Invoked each minute by a host timer (systemd timer or cron), per
SVX-TECH-001 section 3.4. A command invoked by an external timer is preferred
over a long-lived daemon loop for the pilot: it restarts cleanly, it cannot
drift, and a crash is visible to the host rather than leaving a silently wedged
process.

    */1 * * * * cd /srv/scalevexo && docker compose exec -T api \\
        python manage.py run_scheduler_tick

Overlapping runs are safe. Claiming uses SELECT ... FOR UPDATE SKIP LOCKED, so a
second tick starting while the first is still working simply takes different
jobs.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from modules.automation.outbox import dispatch_pending
from modules.automation.worker import process_available_jobs, record_heartbeat
from modules.common.tenancy import workspace_context
from modules.identity.models import Workspace
from modules.reporting import exceptions_service

logger = logging.getLogger("scalevexo.scheduler")


class Command(BaseCommand):
    help = "Dispatch committed outbox events and run due jobs."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-events",
            type=int,
            default=200,
            help="Maximum outbox events to dispatch in this tick.",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=50,
            help="Maximum jobs to execute in this tick.",
        )
        parser.add_argument(
            "--skip-exception-sweep",
            action="store_true",
            help="Dispatch and run jobs without detecting exceptions.",
        )

    def handle(self, *args, **options) -> None:
        dispatched = dispatch_pending(limit=options["max_events"])
        counts = process_available_jobs(max_jobs=options["max_jobs"])

        exceptions: dict[str, int] = {}
        if not options["skip_exception_sweep"]:
            exceptions = self._sweep_exceptions()

        record_heartbeat(
            "scheduler",
            detail={"dispatched": dispatched, **counts, "exceptions": exceptions},
        )

        logger.info(
            "Scheduler tick complete.",
            extra={
                "dispatched": dispatched,
                "jobs": counts,
                "exceptions": exceptions,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Dispatched {dispatched} event(s); jobs: {counts}; "
                f"exceptions raised: {exceptions or 'none'}"
            )
        )

    def _sweep_exceptions(self) -> dict[str, int]:
        """Detect exceptions in each active workspace (CRM10).

        Runs per workspace inside its own context, so the sweep is subject to
        the same row-level security as any other read. One workspace failing
        must not stop the others being swept.
        """
        totals: dict[str, int] = {}
        for workspace_id in Workspace.objects.filter(is_active=True).values_list("id", flat=True):
            try:
                with workspace_context(workspace_id):
                    raised = exceptions_service.sweep()
            except Exception:  # noqa: BLE001 - one tenant must not block the rest
                logger.exception(
                    "Exception sweep failed for a workspace.",
                    extra={"workspace_id": str(workspace_id)},
                )
                continue
            for kind, count in raised.items():
                totals[kind] = totals.get(kind, 0) + count
        return totals
