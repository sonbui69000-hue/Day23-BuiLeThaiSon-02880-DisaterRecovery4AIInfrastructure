"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """TODO: ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "step": n,
        "name": name,
        **kw,
    }
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    print(json.dumps(event), flush=True)
    return event


def confirm(auto: bool, msg: str) -> bool:
    """TODO: auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    if auto:
        return True
    answer = input(f"{msg} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """TODO: 7 bước ở trên."""
    started = time.time()
    result = {"ok": False, "primary": primary, "target": target, "steps": []}

    # 1. Probe both regions before trusting the alert.
    probes = {}
    for region in (primary, target):
        try:
            response = httpx.get(f"{URL[region]}/readyz", timeout=2.0)
            probes[region] = {"ready": response.status_code == 200,
                              "status": response.status_code}
        except httpx.HTTPError as exc:
            probes[region] = {"ready": False, "error": type(exc).__name__}
    step(1, "xac_nhan_outage", probes=probes)
    result["steps"].append("xac_nhan_outage")

    # 2. Start the operator-visible incident clock after confirmation.
    step(2, "thong_bao_incident", incident_started_at=started)
    result["steps"].append("thong_bao_incident")
    if not confirm(auto, f"Fail over traffic from Region {primary} to Region {target}?"):
        step(3, "scale_gpu_pool", ok=False, aborted=True)
        result["steps"].append("scale_gpu_pool")
        result["error"] = "operator declined failover"
        return result

    # 3. Exactly one failover call; all five sub-steps are owned by failover().
    failover_result = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", failover=failover_result)
    result["steps"].append("scale_gpu_pool")

    # 4. Read the returned replica evidence; never call failover again.
    replica = {
        key: failover_result.get(key)
        for key in ("rpo_seconds", "docs_lost", "embed_model_version")
    }
    step(4, "verify_state_replica", replica=replica)
    result["steps"].append("verify_state_replica")

    # 5. Cutover status is also read from the single failover result.
    step(5, "dns_cutover", ok=bool(failover_result.get("ok")),
         target=target, steps=failover_result.get("steps", []))
    result["steps"].append("dns_cutover")

    # 6. Ten real requests against the target, with p95 and error rate.
    latencies = []
    errors = 0
    for _ in range(10):
        began = time.perf_counter()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", params={"q": "hoa don thang 7"}, timeout=5.0)
            if response.status_code != 200 or response.json().get("error"):
                errors += 1
        except (httpx.HTTPError, ValueError):
            errors += 1
        latencies.append((time.perf_counter() - began) * 1000)
    ordered = sorted(latencies)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)] if ordered else None
    signals = {"requests": 10, "errors": errors,
               "error_rate": errors / 10, "p95_ms": round(p95, 2) if p95 is not None else None}
    step(6, "verify_golden_signals", **signals)
    result["steps"].append("verify_golden_signals")

    # 7. Keep the final summary in the runbook timeline.
    elapsed = time.time() - started
    step(7, "post_incident", ok=bool(failover_result.get("ok")) and errors == 0,
         elapsed_s=round(elapsed, 2), measure_command="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    result["steps"].append("post_incident")
    result.update(ok=bool(failover_result.get("ok")) and errors == 0,
                  elapsed_s=round(elapsed, 2), golden_signals=signals,
                  failover=failover_result)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
