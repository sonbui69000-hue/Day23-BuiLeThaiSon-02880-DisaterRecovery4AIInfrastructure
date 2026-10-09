# Postmortem - DR Drill Lab 23

## 1. Timeline

| ISO time | Event | Evidence |
|---|---|---|
| 2026-10-09T03:10:45 | Region A was netblocked and the RTO clock started | `chaos/chaos-events.jsonl:9` |
| 2026-10-09T03:10:45 | First user request failed | `reports/drill-2-withdr.jsonl:58` |
| 2026-10-09T03:11:04 | Health checker marked A unhealthy | `reports/health-events.jsonl:1` |
| 2026-10-09T03:11:22 | Snapshot restore completed | `reports/failover-events.jsonl:30` |
| 2026-10-09T03:11:22 | Region B became ready | `reports/failover-events.jsonl:32` |
| 2026-10-09T03:11:22 | DNS cutover completed | `reports/failover-events.jsonl:33` |
| 2026-10-09T03:11:24 | First successful request from B | `reports/drill-2-withdr.jsonl:77` |

## 2. RTO and RPO

- RTO target: 300s. Measured RTO: 38.8s. Time gap: 261.2s below target.
- RPO target: 300s. Measured RPO: 22.0s. Eleven documents were lost.
- Largest planned delay: health detection at 19.3s, from 5s interval and three
  consecutive failures.
- No ordering warning was reported for this run.

## 3. Root cause, five whys

1. Users saw errors because the edge continued routing to the blocked primary.
2. Recovery waited for the health checker to complete its threshold.
3. Snapshot restore and readiness then delayed cutover until B was safe to serve.
4. Replication lag left newer primary documents outside the restored snapshot.
5. The main remaining risk is replication interval, not failover ordering.

This is a process and guardrail gap, not an operator blame issue.

## 4. Action items

| # | Action | Owner | Deadline | Expected effect |
|---|---|---|---|---|
| 1 | Keep the detection gate in the automated runbook | DR owner | 2026-10-16 | preserve ordering |
| 2 | Add a circuit breaker for `cutover >= detection` | Platform SRE | 2026-10-23 | prevent invalid cutovers |
| 3 | Reduce replication interval after cost review | Data platform | 2026-10-30 | reduce RPO by up to 30s |

## 5. Required questions

1. Detection floor is `5 x 3 = 15s`. It is 39% of the measured 38.8s RTO.
2. A 1s interval would reduce the floor to 3s, saving up to 12s. It increases
   probe load and transient flap risk.
3. `docs_lost=11` means eleven documents present in primary after the last snapshot
   were absent from the restored copy. Customers may see incomplete retrieval.
