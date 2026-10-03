import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Callable

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

class QueueManager:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=config.MAX_QUEUE_LENGTH)
        self.sem = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
        self.jobs: dict = {}
        self.worker_task = None
        self.running = False

    def user_job_count(self, user_id: int) -> int:
        return sum(
            1 for j in self.jobs.values()
            if j.user_id == user_id and j.status in ("pending", "running")
        )

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
        if self.user_job_count(job.user_id) >= config.MAX_JOBS_PER_USER:
            return False, "You already have an active job."
        if self.queue.full():
            return False, "Queue is full. Please try again later."
        self.jobs[job.job_id] = job
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
                    self.queue.task_done()

    async def stop(self):
        self.running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except (asyncio.CancelledError, Exception):
                pass

queue_manager = QueueManager()
