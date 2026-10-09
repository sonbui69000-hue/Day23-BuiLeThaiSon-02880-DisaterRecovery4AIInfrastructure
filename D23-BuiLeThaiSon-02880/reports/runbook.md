# Runbook - Primary Region Down

Run commands from the repository root.

| # | Step | Command | Completion signal | Owner |
|---|---|---|---|---|
| 1 | Confirm outage | `python3 dr/health_checker.py --interval 5 --threshold 3 --duration 300 --out reports/health-events.jsonl` | A has three consecutive failed `/readyz` probes and B is ready | on-call |
| 2 | Open incident and start clock | `python3 -c "import time; print(time.time())"` | timestamp is recorded in the incident ticket and runbook log | incident commander |
| 3 | Restore state and scale pool | `python3 dr/runbook.py --primary a --target b --backend fs --auto` | `reports/failover-events.jsonl` contains steps 1 through 4 | DR operator |
| 4 | Wait for B readiness | `curl -fsS http://127.0.0.1:8002/readyz` | HTTP 200 and `ready:true` | DR operator |
| 5 | Verify DNS cutover | `curl -fsS http://127.0.0.1:8080/edge/state` | response contains `active_region:b` | DR operator |
| 6 | Check golden signals | `for i in (seq 1 10); curl -fsS 'http://127.0.0.1:8080/v1/infer?q=healthcheck' >/dev/null; or exit 1; end` | 10 of 10 requests pass, error rate is 0% | SRE on-call |
| 7 | Measure and close incident | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | RTO and RPO are non-null, then update postmortem | incident commander |

## Rollback

Rollback only if B has sustained errors, bad data, or unacceptable latency for
10 minutes, and A passes `/readyz`. The incident commander approves rollback.
The SRE operator executes:

```fish
curl -fsS http://127.0.0.1:8001/readyz
python3 -c "open('edge/active_region','w',encoding='utf-8').write('a')"
curl -fsS http://127.0.0.1:8080/edge/state
```

If A is not ready, keep traffic on B and do not rollback.
