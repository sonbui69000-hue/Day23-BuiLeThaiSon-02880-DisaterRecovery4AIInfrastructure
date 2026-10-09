"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
                       (§3: "backup index nhưng quên backup embedding model version
                        -> index không tương thích khi restore")
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200. Region phụ có WARMUP_SECONDS —
                       đây là GPU pool warm-up của §4, nó nằm trong RTO của bạn.
  5_dns_cutover      — ghi region đích vào edge/active_region

BẪY: nếu bạn đổi edge/active_region TRƯỚC bước 4, user sẽ nhận 503 từ CẢ HAI region
và RTO của bạn dài hơn, không ngắn hơn. Nếu bước 4 timeout -> ABORT, KHÔNG cutover.

Mỗi bước ghi 1 dòng vào reports/failover-events.jsonl với ts + step.
Không có dòng 5_dns_cutover = tools/measure_rto.py không tìm được t_cutover = mất điểm.

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """TODO: append 1 dòng JSONL có ts + iso vào LOG, và print ra stdout."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **kw}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    print(json.dumps(event), flush=True)
    return event


def state_of(region: str) -> dict:
    """Read the local state needed to verify that a target region exists."""
    root = pathlib.Path(f"state/region-{region}")
    return {
        "region": region,
        "exists": root.exists(),
        "pool_state": (root / "pool_state").read_text().strip()
        if (root / "pool_state").exists() else "cold",
        "vectors": (root / "vectors.sqlite").exists(),
        "weights": (root / "weights" / "model.bin").exists(),
    }


def failover(target: str, backend: str, wait: float) -> dict:
    """TODO: 5 bước ở trên, đúng thứ tự."""
    if target not in URL:
        return {"ok": False, "error": f"unknown target region: {target}", "steps": []}

    steps = []
    emit(step="1_verify_target", target=target)
    steps.append("1_verify_target")
    target_state = state_of(target)
    if not target_state.get("exists", True):
        return {"ok": False, "error": "target region does not exist", "steps": steps}

    meta = snapshot.get(target, backend)
    primary_db = pathlib.Path("state/region-a/vectors.sqlite")
    restored_db = pathlib.Path(f"state/region-{target}/vectors.sqlite")
    rpo = snapshot.rpo(primary_db, restored_db) if primary_db.exists() and restored_db.exists() else {
        "rpo_seconds": None, "docs_lost": None}
    result = {
        "ok": False,
        "steps": steps,
        "rpo_seconds": rpo.get("rpo_seconds"),
        "docs_lost": rpo.get("docs_lost"),
        "embed_model_version": meta.get("embed_model_version"),
    }
    emit(step="2_restore_snapshot", target=target, **{
        "rpo_seconds": result["rpo_seconds"],
        "docs_lost": result["docs_lost"],
        "embed_model_version": result["embed_model_version"],
    })
    steps.append("2_restore_snapshot")

    emit(step="3_scale_pool", target=target)
    steps.append("3_scale_pool")
    pathlib.Path(f"state/region-{target}/pool_state").write_text("full\n", encoding="utf-8")

    deadline = time.monotonic() + wait
    ready = False
    reason = "timeout"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{URL[target]}/readyz", timeout=min(2.0, max(0.1, wait)))
            if response.status_code == 200:
                ready = True
                reason = "ready"
                break
            reason = f"status={response.status_code}"
        except httpx.HTTPError as exc:
            reason = f"{type(exc).__name__}: {exc}"
        time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))
    if not ready:
        emit(step="4_wait_ready", target=target, ok=False, reason=reason)
        steps.append("4_wait_ready")
        result.update(ok=False, error="target did not become ready", reason=reason)
        return result

    emit(step="4_wait_ready", target=target, ok=True, reason=reason)
    steps.append("4_wait_ready")
    emit(step="5_dns_cutover", target=target)
    steps.append("5_dns_cutover")
    pathlib.Path("edge/active_region").write_text(target, encoding="utf-8")
    result.update(ok=True)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))
