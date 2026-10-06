import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from bot import config
from bot.logger import get_logger

log = get_logger(__name__)

@dataclass
class Job:
    job_id: str
    user_id: int
    func: Callable
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    status: str = "pending"
    file_size: str = ""
    file_name: str = ""
    chat_id: int = 0
    message_id: int = 0
    cancel_requested: bool = False
    # Stored for DB persistence / crash recovery
    src_chat_id: int = 0
    src_message_id: int = 0
    media_file_id: str = ""

    def to_doc(self) -> dict:
        """Serialise to a MongoDB document (no Callable/asyncio objects)."""
        return {
            "job_id":         self.job_id,
            "user_id":        self.user_id,
            "status":         self.status,
            "file_size":      self.file_size,
            "file_name":      self.file_name,
            "chat_id":        self.chat_id,
            "message_id":     self.message_id,
            "src_chat_id":    self.src_chat_id,
            "src_message_id": self.src_message_id,
            "media_file_id":  self.media_file_id,
        }

class QueueManager:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=config.MAX_QUEUE_LENGTH)
        self.sem = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
        self.jobs: dict = {}
        self.worker_task = None
        self.running = False

    def user_running_count(self, user_id: int) -> int:
        return sum(1 for j in self.jobs.values()
                   if j.user_id == user_id and j.status == "running")

    def user_queue_count(self, user_id: int) -> int:
        return sum(1 for j in self.jobs.values()
                   if j.user_id == user_id and j.status in ("pending", "running"))

    # Keep old name for any existing call-sites
    def user_job_count(self, user_id: int) -> int:
        return self.user_queue_count(user_id)

    def get_user_jobs(self, user_id: int):
        return [
            j for j in self.jobs.values()
            if j.user_id == user_id and j.status in ("pending", "running")
        ]

    def get_queue_status(self):
        return [(j.job_id, j.file_name, j.status) for j in self.jobs.values()]

    def queue_position(self, job: Job) -> int:
        pos = 1
        for j in self.jobs.values():
            if j.status not in ("pending", "running"):
                continue
            if j.status == "running":
                pos += 1
            elif j.status == "pending" and j.job_id != job.job_id:
                pos += 1
        return pos

    async def add_job(self, job: Job):
        if self.user_queue_count(job.user_id) >= config.MAX_QUEUED_JOBS_PER_USER:
            return False, f"Your queue is full ({config.MAX_QUEUED_JOBS_PER_USER} files max). Please wait."
        if self.queue.full():
            return False, "Global queue is full. Please try again later."
        self.jobs[job.job_id] = job
        # Persist to DB before queuing so crash recovery can find it
        try:
            import database
            await database.save_job(job.to_doc())
        except Exception as e:
            log.warning("Could not persist job %s to DB: %s", job.job_id, e)
        await self.queue.put(job)
        return True, self.queue_position(job)

    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.status == "running":
            job.cancel_requested = True
            return True
        if job.status == "pending":
            job.status = "cancelled"
            return True
        return False

    async def start_worker(self):
        self.running = True
        self.worker_task = asyncio.create_task(self._worker())
        log.info("Queue worker started.")

    async def _worker(self):
        while self.running:
            try:
                job: Job = await self.queue.get()
            except asyncio.CancelledError:
                break

            if job.status == "cancelled":
                self.queue.task_done()
                continue

            async with self.sem:
                job.status = "running"
                try:
                    import database
                    await database.save_job(job.to_doc())
                except Exception as e:
                    log.warning("Could not update job %s status in DB: %s", job.job_id, e)
                try:
                    await job.func(*job.args, **job.kwargs)
                    if not job.cancel_requested and job.status not in ("cancelled", "failed"):
                        job.status = "completed"
                except asyncio.CancelledError:
                    job.status = "cancelled"
                    raise
                except Exception as e:
                    log.exception("Job %s failed: %s", job.job_id, e)
                    job.status = "failed"
                finally:
                    # Mark done in DB so it won't be recovered on next restart
                    try:
                        import database
                        await database.mark_job_done(job.job_id, job.status)
                    except Exception as e:
                        log.warning("Could not mark job %s done in DB: %s", job.job_id, e)
                    self.queue.task_done()

    async def stop(self, timeout: float = 3600.0):
        """
        Graceful shutdown:
        1. Stop accepting new jobs from the queue.
        2. Wait up to `timeout` seconds for any currently-running job to finish.
        3. Only then cancel the worker task.

        Heroku sends SIGTERM and gives 30 s before SIGKILL, but a dyno restart
        triggered by a deploy waits until the process exits. We set a generous
        timeout so a long encode is never killed mid-way by a deploy.
        """
        self.running = False

        # Wait for all running jobs to complete
        running_jobs = [j for j in self.jobs.values() if j.status == "running"]
        if running_jobs:
            log.info(
                "Graceful shutdown: waiting for %d running job(s) to finish "
                "(timeout=%.0fs)…",
                len(running_jobs), timeout,
            )
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                still_running = [j for j in self.jobs.values() if j.status == "running"]
                if not still_running:
                    log.info("All jobs finished. Shutting down.")
                    break
                if asyncio.get_event_loop().time() >= deadline:
                    log.warning(
                        "Shutdown timeout reached; %d job(s) still running — "
                        "forcing exit.", len(still_running)
                    )
                    for j in still_running:
                        j.cancel_requested = True
                    await asyncio.sleep(2)
                    break
                await asyncio.sleep(1)

        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except (asyncio.CancelledError, Exception):
                pass

queue_manager = QueueManager()
