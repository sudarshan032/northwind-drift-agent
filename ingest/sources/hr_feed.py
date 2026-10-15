"""hr_feed: HR worker export (Workday-style report).

Raw (data/raw/hr_feed/workers.csv): RaaS-style report columns: Employee_ID
(the pseudonymous worker ID), Worker_Type, Worker_Status, the Workday status
flags Active / Terminated / On_Leave (Workday has no single status field; a
worker on leave is still Active), Hire_Date, Termination_Date,
Effective_Date, Supervisory_Organization. No names or emails.

Connector: status from the flags: Terminated -> terminated, On_Leave ->
leave, else active.
"""

import os

from ingest.sources.common import raw_dir, read_csv, write_csv

COLUMNS = ["Employee_ID", "Worker_Type", "Worker_Status", "Active", "Terminated", "On_Leave", "Hire_Date",
           "Termination_Date", "Effective_Date", "Supervisory_Organization"]


def write(workers, out):
    write_csv(os.path.join(raw_dir(out, "hr_feed"), "workers.csv"), workers, COLUMNS)
    return len(workers)


def read(data):
    return [{"source": "hr_feed", "source_record": f"workers.csv#{i + 2}", "id": r["Employee_ID"],
             "status": "terminated" if r["Terminated"] == "1" else "leave" if r["On_Leave"] == "1" else "active",
             "effective_date": r["Effective_Date"]}
            for i, r in enumerate(read_csv(os.path.join(data, "raw", "hr_feed", "workers.csv")))]
