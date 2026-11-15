"""Running the worker: `p42cloud-worker` (the installation's `worker` service).

Every few seconds it queues the runs of due schedules, then delivers outside calls and works
through the queue of runs, one at a time; runs a stopped worker left behind are ended. Several
workers may run side by side: each slot, run and call is claimed by exactly one. It stops
cleanly on SIGTERM, after the current run.
"""

import argparse
import asyncio
import contextlib
import logging
import signal
import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.data import installation
from p42cloud.kernel.config import Settings, get_settings
from p42cloud.kernel.db import create_engine
from p42cloud.kernel.secrets import Sealer, use_sealer
from p42cloud.modules.core import CORE
from p42cloud.modules.install import compose
from p42cloud.modules.registry import discover
from p42cloud.services.announcements.service import notify_due
from p42cloud.services.calls.service import deliver
from p42cloud.services.calls.service import recover as recover_calls
from p42cloud.services.jobs.service import claim, execute, recover, tick
from p42cloud.services.languages.service import apply_changed
from p42cloud.services.notifications import telegram

log = logging.getLogger("p42cloud.worker")
IDLE = 2.0  # seconds between looks when there's nothing to do
TICK = timedelta(seconds=5)
RECOVER = timedelta(minutes=1)
SETTINGS_EVERY = timedelta(seconds=installation.REFRESH)


async def work(stop: asyncio.Event, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    engine = create_engine(settings.database_url)
    compose([CORE, *discover(settings.modules_dir)])
    if settings.master_key_file is not None:  # without it, jobs can't open the vault
        use_sealer(Sealer.from_file(settings.master_key_file))
    name = f"{socket.gethostname()}:{id(stop)}"
    last_tick = last_recover = last_poll = last_settings = datetime.min.replace(tzinfo=UTC)
    try:
        while not stop.is_set():
            now = datetime.now(UTC)
            if now - last_recover >= RECOVER:
                await _each_minute(engine, now)
                last_recover = now
            if now - last_settings >= SETTINGS_EVERY:
                await installation.refresh(engine)  # the app's address, for links in messages
                last_settings = now
            if now - last_tick >= TICK:
                await tick(engine, now)
                last_tick = now
            # The bot is asked every few seconds while someone links a chat, else each minute.
            if now - last_poll >= TICK and (
                now - last_poll >= RECOVER or await telegram.waiting(engine)
            ):
                await telegram.poll(engine)
                last_poll = now
            sent = await telegram.deliver(engine, installation.public_url(settings))
            # One call, then one run, in turn: neither queue can starve the other.
            if (state := await deliver(engine)) is not None:
                log.info("outside call: %s", state)
            run = await claim(engine, name)
            if run is not None:
                state = await execute(engine, run)
                log.info("%s (attempt %d): %s", run.job, run.attempt, state)
            elif state is None and sent is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), IDLE)
    finally:
        await engine.dispose()


async def _each_minute(engine: AsyncEngine, now: datetime) -> None:
    """What a stopped worker left behind: runs end, calls are asked about; messages whose time
    has come go out as notifications; translations changed by hand are shown."""
    if ended := await recover(engine, now):
        log.warning("ended %d runs a stopped worker left behind", ended)
    if lost := await recover_calls(engine, now):
        log.warning("%d outside calls lost their answer; asking first", lost)
    if notified := await notify_due(engine):
        log.info("%d messages sent as notifications", notified)
    if shown := await apply_changed(engine):
        log.info("%d texts got their changed translations", shown)


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
