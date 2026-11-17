"""Day 5 — fleet: many independent agents at once.

Concept taught: once an agent is one object with ``run(task)``, parallelism is
a thread pool. Each job gets its own harness over its own workdir; the agents
share nothing, so they cannot step on each other.

Design rules this file embodies:
  * One failure never sinks the fleet. A job that raises becomes a result with
    ``ok: False`` and the error as its report.
  * Results come back in the order the jobs went in, whatever order they
    finished in.
"""

from concurrent.futures import ThreadPoolExecutor


def run_fleet(jobs, make_harness, max_workers=4):
    """Run ``[{name, workdir, task}]`` in parallel; return one result per job."""
    def run_one(job):
        try:
            report = make_harness(job["workdir"]).run(job["task"])
            return {"name": job["name"], "ok": True, "report": report}
        except Exception as error:
            return {"name": job["name"], "ok": False,
                    "report": "%s: %s" % (type(error).__name__, error)}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(run_one, jobs))  # map keeps input order
