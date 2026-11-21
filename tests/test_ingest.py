import pytest
from decimal import Decimal
from app import store
from app.ingest import service
from app.ingest.demo import demo_batches
from app.errors import DomainError
@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    service.reset_demo()
    yield
    service.reset_demo()
def _payload(events):
    return {"events": events}
def _ev(eid, card="C1", minute=0, route="01", reported="2.50"):
    return {"event_id": eid, "card": card, "route": route, "result": "success",
            "event_time": f"2026-11-16T{8+minute//60:02d}:{minute%60:02d}:00+08:00",
            "reported": reported, "note": "t"}
def _post(client, device, seq, payload, **extra):
    body = {"payload": payload, "checksum": service.checksum(payload), **extra}
    return client.post(f"/api/ingest/devices/{device}/segments/{seq}", json=body)
def test_watermark_only_advances_on_contiguous():
    "裁决：越序段不推进水位，只入待补台账"
    service.ingest_segment("DEV-A", 1, _payload([_ev("a1")]))
    service.ingest_segment("DEV-A", 2, _payload([_ev("a2")]))
    led = service.ingest_segment("DEV-A", 4, _payload([_ev("a4")]))
    assert led["contiguous_seq"] == 2
    assert led["gap_ranges"] == [[3, 3]]
    assert [s["seq"] for s in led["pending_segments"]] == [4]
    assert led["continuous_range"] == [1, 2]
def test_gap_fill_promotes_pending_and_recomputes():
    "缺口补齐后连续水位一次推进，待补段转为连续口径"
    service.ingest_segment("DEV-A", 1, _payload([_ev("a1", minute=0)]))
    service.ingest_segment("DEV-A", 3, _payload([_ev("a3", minute=40)]))
    led = service.ingest_segment("DEV-A", 2, _payload([_ev("a2", minute=20, route="02")]))
    assert led["contiguous_seq"] == 3 and led["gap_ranges"] == []
    assert led["pending_segments"] == []
    metrics = led["receivables"]["metrics"]
    assert metrics["confirmed_events"] == 3 and metrics["pending_events"] == 0
    assert Decimal(metrics["confirmed_total"]) > 0
def test_retry_same_segment_is_idempotent():
    "同一补传重试不重复占额：版本与事件数不变，重试计数累加"
    payload = _payload([_ev("a1", minute=0), _ev("a2", minute=20, route="02")])
    first = service.ingest_segment("DEV-A", 1, payload, service.checksum(payload))
    again = service.ingest_segment("DEV-A", 1, payload, service.checksum(payload))
    third = service.ingest_segment("DEV-A", 1, payload, service.checksum(payload))
    assert first["version"] == again["version"] == third["version"]
    seg = next(s for s in third["segments"] if s["seq"] == 1)
    assert seg["duplicates"] == 2
    assert third["receivables"]["metrics"]["confirmed_events"] == 2
def test_same_seq_different_checksum_conflicts():
    payload = _payload([_ev("a1")])
    service.ingest_segment("DEV-A", 1, payload, service.checksum(payload))
    other = _payload([_ev("a1"), _ev("a9")])
    with pytest.raises(DomainError) as e:
        service.ingest_segment("DEV-A", 1, other, service.checksum(other))
    assert e.value.code == "SEGMENT_CONFLICT"
    led = service.get_ledger("DEV-A")
    assert led["receivables"]["metrics"]["confirmed_events"] == 1
def test_stale_expected_version_keeps_old_watermark():
    "水位切换失败保留旧版：409 且不写入"
    service.ingest_segment("DEV-A", 1, _payload([_ev("a1")]))
    with pytest.raises(DomainError) as e:
        service.ingest_segment("DEV-A", 2, _payload([_ev("a2")]), expected_version=99)
    assert e.value.status == 409 and e.value.code == "STALE_VERSION"
    led = service.get_ledger("DEV-A")
    assert led["contiguous_seq"] == 1 and led["version"] == 1
    assert [s["seq"] for s in led["segments"]] == [1]
def test_checksum_mismatch_rejected():
    with pytest.raises(DomainError) as e:
        service.ingest_segment("DEV-A", 1, _payload([_ev("a1")]), client_checksum="deadbeef")
    assert e.value.code == "CHECKSUM_MISMATCH"
    with pytest.raises(DomainError):
        service.get_ledger("DEV-A")
def test_seal_blocked_while_gap_open_and_allowed_after_fill():
    "缺口未核清前不能封存日结"
    service.ingest_segment("DEV-A", 1, _payload([_ev("a1", minute=0)]))
    service.ingest_segment("DEV-A", 2, _payload([_ev("a2", minute=20, route="02")]))
    service.ingest_segment("DEV-A", 4, _payload([_ev("a4", minute=50)]))
    with pytest.raises(DomainError) as e:
        service.seal_day("DEV-A", "2026-11-16")
    assert e.value.status == 409 and e.value.code == "GAP_OPEN"
    service.ingest_segment("DEV-A", 3, _payload([_ev("a3", minute=35, route="03")]))
    sealed = service.seal_day("DEV-A", "2026-11-16")
    assert sealed["idempotent"] is False and len(sealed["snapshot_sha256"]) == 64
    again = service.seal_day("DEV-A", "2026-11-16")
    assert again["idempotent"] is True and again["snapshot_sha256"] == sealed["snapshot_sha256"]
def test_pending_events_are_exposure_not_final_receivable():
    "待补段只计基础票价敞口，不参与换乘/封顶定稿"
    service.ingest_segment("DEV-A", 1, _payload([_ev("a1", "C1", 0), _ev("a2", "C1", 20, "02")]))
    service.ingest_segment("DEV-A", 3, _payload([_ev("a9", "C1", 30, "03")]))
    led = service.get_ledger("DEV-A")
    m = led["receivables"]["metrics"]
    assert m["confirmed_events"] == 2 and m["pending_events"] == 1
    assert Decimal(m["pending_exposure"]) == Decimal("2.50")
    day = led["receivables"]["days"][0]
    assert day["settleable"] is False and day["status"] == "缺口未核清·暂缓封存"
def test_quarantine_does_not_disappear_in_watermark():
    service.ingest_segment("DEV-A", 1, _payload([
        {"event_id": "q1", "card": "C1", "route": "X9", "result": "success",
         "event_time": "2026-11-16T08:00:00+08:00", "reported": "2.50"},
        _ev("ok1")]))
    led = service.get_ledger("DEV-A")
    reasons = {q["reason"] for q in led["receivables"]["confirmed_quarantine"]}
    assert "UNKNOWN_ROUTE" in reasons
    assert led["receivables"]["metrics"]["confirmed_events"] == 1
def test_api_flow_and_409_via_client(client):
    batches = demo_batches()
    seg = batches["OB-101"][1]
    ok = _post(client, "OB-101", 1, seg["payload"])
    assert ok.status_code == 200 and ok.get_json()["contiguous_seq"] == 1
    dup = _post(client, "OB-101", 1, seg["payload"])
    assert dup.status_code == 200 and dup.get_json()["version"] == 1
    stale = _post(client, "OB-101", 2, batches["OB-101"][2]["payload"], expected_version=1)
    assert stale.status_code == 200
    bad = client.post("/api/ingest/devices/OB-101/segments/9",
                      json={"payload": {"events": []}, "checksum": "nope"})
    assert bad.status_code == 422 and bad.get_json()["code"] == "CHECKSUM_MISMATCH"
    listing = client.get("/api/ingest/devices").get_json()["devices"]
    assert {d["device_id"] for d in listing} == {"OB-101"}
def test_archive_contains_watermark_and_days(client):
    import csv, io
    demo = demo_batches()
    for seq in (1, 2, 4):
        seg = demo["OB-101"][seq]
        client.post(f"/api/ingest/devices/OB-101/segments/{seq}",
                    json={"payload": seg["payload"], "checksum": seg["checksum"]})
    raw = client.get("/api/ingest/devices/OB-101/archive").data.decode("utf-8-sig")
    text = raw
    assert "待补段" in text and "缺口未核清" in text and "连续区段" in text
def test_demo_seed_is_idempotent_and_tells_gap_story():
    from app.ingest.demo import seed_demo
    assert seed_demo() is True and seed_demo() is False
    led = service.get_ledger("OB-101")
    assert led["contiguous_seq"] == 2 and led["gap_ranges"] == [[3, 3]]
    assert [s["seq"] for s in led["pending_segments"]] == [4, 5]
    good = service.get_ledger("OB-102")
    assert good["contiguous_seq"] == 3 and good["gap_ranges"] == []
    sealed = service.seal_day("OB-102", "2026-11-16")
    assert sealed["idempotent"] is False
