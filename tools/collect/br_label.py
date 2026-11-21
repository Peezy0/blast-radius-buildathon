"""pytest plugin: write the set of failing node IDs (any phase, any subtest,
collection errors expand to the whole file) to $BR_LABEL_OUT."""
import json, os

_failed = set()
_collect_errors = set()

def pytest_runtest_logreport(report):
    if report.failed:
        _failed.add(report.nodeid)

def pytest_collectreport(report):
    if report.failed:
        _collect_errors.add(report.nodeid.split("::")[0])

def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        return
    with open(os.environ["BR_LABEL_OUT"], "w") as f:
        json.dump({"failed": sorted(_failed), "collect_errors": sorted(_collect_errors),
                   "exitstatus": int(exitstatus)}, f)
