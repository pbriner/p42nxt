"""Running the worker: `p42cloud-worker` (the installation's `worker` service).

Every few seconds it queues the runs of due schedules, then works through the queue one run at
a time; runs a stopped worker left behind are ended. Several workers may run side by side: each
slot and run is claimed by exactly one. It stops cleanly on SIGTERM, after the current run.
"""

import argparse
import asyncio
import contextlib
import logging
import signal
import socket
from datetime import UTC, datetime, timedelta

from p42cloud.kernel.config import Settings, get_settings
from p42cloud.kernel.db import create_engine
from p42cloud.modules.core import CORE
from p42cloud.modules.install import compose
from p42cloud.modules.registry import discover
from p42cloud.services.jobs.service import claim, execute, recover, tick

log = logging.getLogger("p42cloud.worker")
IDLE = 2.0  # seconds between looks when there's nothing to do
TICK = timedelta(seconds=5)
RECOVER = timedelta(minutes=1)


async def work(stop: asyncio.Event, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    engine = create_engine(settings.database_url)
    compose([CORE, *discover()])
    name = f"{socket.gethostname()}:{id(stop)}"
    last_tick = last_recover = datetime.min.replace(tzinfo=UTC)
    try:
        while not stop.is_set():
            now = datetime.now(UTC)
            if now - last_recover >= RECOVER:
                if ended := await recover(engine, now):
                    log.warning("ended %d runs a stopped worker left behind", ended)
                last_recover = now
            if now - last_tick >= TICK:
                await tick(engine, now)
                last_tick = now
            run = await claim(engine, name)
            if run is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), IDLE)
                continue
            state = await execute(engine, run)
            log.info("%s (attempt %d): %s", run.job, run.attempt, state)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="p42cloud-worker", description="Runs jobs and schedules."
    ).parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for name in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(name, stop.set)
        await work(stop)

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
