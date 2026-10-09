# RTO/RPO Evidence - Lab 23

This report uses the latest drill logs. The latest run has no measurement warnings.

## 1. Drill 1 - no DR

| Metric | Value | Method | Evidence |
|---|---:|---|---|
| t_outage | 2026-10-09T03:10:45 | chaos kill | `chaos/chaos-events.jsonl:9` |
| First failed request | +0.48s | first `ok:false` after outage | `reports/drill-1-nodr.jsonl:18` |
| Later success | none | no later `ok:true` | `reports/drill-1-nodr.jsonl:33` |
| RTO | NO_RECOVERY | measured baseline | `reports/drill-1-nodr.jsonl:33` |

## 2. Drill 2 - DR enabled

| Milestone | Seconds from t_outage | Evidence |
|---|---:|---|
| t_outage | 0.00 | `chaos/chaos-events.jsonl:9` |
| First user error | +0.25s | `reports/drill-2-withdr.jsonl:58` |
| Health check detected A | +19.31s | `reports/health-events.jsonl:1` |
| Snapshot restore complete | +37.26s | `reports/failover-events.jsonl:30` |
| Region B ready | +37.29s | `reports/failover-events.jsonl:32` |
| DNS cutover | +37.29s | `reports/failover-events.jsonl:33` |
| First success from B | +38.76s | `reports/drill-2-withdr.jsonl:77` |

| Metric | Measured | Target | Verdict |
|---|---:|---:|---|
| RTO | 38.8s | 300s | PASS |
| RPO | 22.0s / 11 docs | 300s | PASS |

Detection preceded cutover, and B served recovery traffic.

## 3. RTO breakdown

| Component | Seconds | Evidence | Reduction |
|---|---:|---|---|
| Health check detect floor | 15.0 | `reports/health-events.jsonl:1` | lower interval with flap protection |
| Snapshot restore | 0.05 | `reports/failover-events.jsonl:30` | smaller or incremental snapshots |
| GPU pool warm-up | 0.03 observed | `reports/failover-events.jsonl:32` | pre-warm capacity |
| DNS/LB cache | 1.47 | `reports/drill-2-withdr.jsonl:77` | lower edge TTL |

The measured RTO is 38.8s. All milestones come from the latest run.
