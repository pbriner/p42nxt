# Jobs and schedules

Work that runs without anyone waiting for it: on a schedule, or when someone starts it. The
installation's **worker** (`p42cloud-worker`, the `worker` service; the same image as the app)
runs it. The queue is in PostgreSQL; nothing else is needed.

## Declaring

```python
async def unpin_old(run: JobRun) -> str:
    client = Client(run.records)
    ...
    return "3 unpinned."

jobs=(Job("unpin_old", "Unpin old greetings", unpin_old, timeout=60),),
schedules=(Schedule("unpin_old", "Old pins, nightly", "unpin_old", "41 4 * * *"),),
```

| Part | Meaning |
|---|---|
| `Job` | The work: its code, a time limit (`timeout`, seconds, up to 3600) and how often a failed run is tried again (`retries`, up to 10) |
| `Schedule` | When a job runs: cron (minute, hour, day of month, month, day of week; `*`, lists, ranges, steps), in a `time_zone`; `missed`: `skip` (default) or `run_once` for slots missed while no worker ran |

They become records of `sys.job` and `sys.schedule`. Administrators may change a schedule's
time, time zone and missed runs, or pause it; upgrades keep what they set.

## Running

- **Every few seconds** the worker queues a run for each due schedule. A schedule doesn't start
  while its previous run is still going: that slot is recorded as skipped.
- **A run is one transaction**: committed when the job returns, rolled back when it fails or
  takes longer than its time limit. What it returns is the run's result.
- **Failed runs are tried again**, the first time after 30 seconds, then waiting twice as long
  each time, up to the job's retries.
- **Several workers** may run side by side: each schedule slot and each run is taken by exactly
  one of them. Runs a stopped worker left behind are ended as failed.
- **Whose rights**: a module's jobs act as the module's service account (`<module> jobs`, in a
  group holding the module's roles); the core's jobs act as the system. Every change a job makes
  carries the channel `job:<module>.<job>` and the run's ID in the audit log.

Every run is a record of `sys.job_run`: its job and schedule, state (queued, running, succeeded,
failed, skipped), who started it (its schedule, someone, a retry), attempt, times and result.

## In the app

Platform → Automation: **Schedules** (with **Run now**, **Pause**, **Resume**) and **Job runs**.

## The core's jobs

| Job | When | What |
|---|---|---|
| `sys.repair_groups` | Nightly, 02:23 UTC | Dynamic groups' members from their rules: for rules on time, and repairs |

## Testing a module's jobs

The SDK's test plugin has `run_job("<module>.<job>")`: it runs the job at once as the worker
would, and returns its state and result.
