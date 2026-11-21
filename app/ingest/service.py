import hashlib, json
from datetime import datetime, timezone
from decimal import Decimal
from app.errors import DomainError
from app.store import connect
from app.ingest.db import ensure_schema
from app.ingest.receivables import recompute, business_day_of
def _now():
    return datetime.now(timezone.utc).isoformat()
def checksum(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
def _segment_day(seg):
    days = sorted({business_day_of(e) for e in (seg.get("events") or []) if isinstance(e, dict)})
    return ",".join(days) if days else "无事件"
def _ranges(seqs):
    if not seqs:
        return []
    out, start, prev = [], seqs[0], seqs[0]
    for s in seqs[1:]:
        if s == prev + 1:
            prev = s
        else:
            out.append([start, prev]); start = prev = s
    out.append([start, prev])
    return out
def ingest_segment(device_id, seq, payload, client_checksum=None, expected_version=None):
    """裁决：水位只认连续到达。越序段只入待补台账，不推进水位、不进定稿。"""
    if not isinstance(device_id, str) or not device_id.strip():
        raise DomainError("BAD_DEVICE", "车载机标识不能为空")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise DomainError("BAD_SEQ", "段序号需要正整数")
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise DomainError("BAD_PAYLOAD", "段载荷需要含 events 列表的对象")
    if any(not isinstance(e, dict) for e in payload["events"]):
        raise DomainError("BAD_PAYLOAD", "段内事件需要对象")
    digest = checksum(payload)
    if client_checksum is not None and client_checksum != digest:
        raise DomainError("CHECKSUM_MISMATCH", "校验和与载荷不一致，拒绝写入")
    if expected_version is not None and (not isinstance(expected_version, int) or isinstance(expected_version, bool)):
        raise DomainError("BAD_VERSION", "期望水位版本需要整数")
    ensure_schema()
    with connect() as db:
        wm = db.execute("SELECT contiguous_seq,version FROM device_watermarks WHERE device_id=?", (device_id,)).fetchone()
        current_version = wm[1] if wm else 1
        if expected_version is not None and expected_version != current_version:
            raise DomainError("STALE_VERSION", "水位版本已变化，本次切换作废，旧版水位保留", 409)
        existing = db.execute("SELECT checksum FROM device_segments WHERE device_id=? AND seq=?", (device_id, seq)).fetchone()
        if existing:
            if existing[0] != digest:
                raise DomainError("SEGMENT_CONFLICT", "同序号段内容不一致，禁止覆盖", 422)
            db.execute("UPDATE device_segments SET duplicates=duplicates+1 WHERE device_id=? AND seq=?", (device_id, seq))
            changed = False
        else:
            db.execute("INSERT INTO device_segments(device_id,seq,checksum,payload,received_at) VALUES(?,?,?,?,?)",
                       (device_id, seq, digest, json.dumps(payload, ensure_ascii=False), _now()))
            changed = True
        rows = db.execute("SELECT seq FROM device_segments WHERE device_id=?", (device_id,)).fetchall()
        have = sorted(s for (s,) in rows)
        contig = 0
        for s in have:
            if s == contig + 1:
                contig = s
            else:
                break
        if wm is None:
            db.execute("INSERT INTO device_watermarks(device_id,contiguous_seq,version,updated_at) VALUES(?,?,1,?)",
                       (device_id, contig, _now()))
        elif changed:
            db.execute("UPDATE device_watermarks SET contiguous_seq=?,version=version+1,updated_at=? WHERE device_id=?",
                       (contig, _now(), device_id))
        state = _ledger(db, device_id)
    return state
def _ledger(db, device_id):
    wm = db.execute("SELECT contiguous_seq,version,updated_at FROM device_watermarks WHERE device_id=?", (device_id,)).fetchone()
    if not wm:
        raise DomainError("NOT_FOUND", "车载机暂无采集记录", 404)
    seg_rows = db.execute("SELECT seq,checksum,payload,received_at,duplicates FROM device_segments WHERE device_id=? ORDER BY seq",
                          (device_id,)).fetchall()
    segments = [{"seq": s, "checksum": cs, "payload": json.loads(p), "received_at": ra, "duplicates": dup,
                 "business_days": _segment_day(json.loads(p))} for s, cs, p, ra, dup in seg_rows]
    contig = wm[0]
    pending = sorted(s["seq"] for s in segments if s["seq"] > contig)
    promoted = [s for s in segments if s["seq"] <= contig]
    gaps = _ranges(list(range(contig + 1, (pending[0] if pending else contig + 1))))
    money = recompute([{"seq": s["seq"], "payload": s["payload"]} for s in promoted],
                      [{"seq": s["seq"], "payload": s["payload"]} for s in segments if s["seq"] in pending])
    settlements = [{"business_day": d, "sealed_at": t}
                   for d, t in db.execute("SELECT business_day,sealed_at FROM day_settlements WHERE device_id=? ORDER BY business_day",
                                          (device_id,)).fetchall()]
    for day in money["days"]:
        day["sealed"] = day["business_day"] in {s["business_day"] for s in settlements}
    return {"device_id": device_id, "contiguous_seq": contig, "version": wm[1], "updated_at": wm[2],
            "continuous_range": [1, contig] if contig else [],
            "pending_segments": [s for s in segments if s["seq"] in pending],
            "gap_ranges": gaps, "segments": segments, "settlements": settlements,
            "receivables": money}
def get_ledger(device_id):
    ensure_schema()
    with connect() as db:
        return _ledger(db, device_id)
def list_devices():
    ensure_schema()
    with connect() as db:
        ids = [r[0] for r in db.execute("SELECT device_id FROM device_watermarks ORDER BY device_id").fetchall()]
    return [get_ledger(i) for i in ids]
def seal_day(device_id, business_day):
    if not isinstance(business_day, str) or len(business_day) != 10:
        raise DomainError("BAD_DAY", "业务日需要 YYYY-MM-DD")
    ensure_schema()
    with connect() as db:
        ledger = _ledger(db, device_id)
        if ledger["pending_segments"]:
            raise DomainError("GAP_OPEN", "缺口未核清，待补段未连续，不能封存日结", 409)
        day = next((d for d in ledger["receivables"]["days"] if d["business_day"] == business_day), None)
        if day is None:
            raise DomainError("DAY_NO_DATA", "该业务日没有连续区段事件，无法封存", 422)
        if not day["settleable"]:
            raise DomainError("GAP_OPEN", "该卡日仍含待补事件，不能封存日结", 409)
        prior = db.execute("SELECT sealed_at,snapshot FROM day_settlements WHERE device_id=? AND business_day=?",
                           (device_id, business_day)).fetchone()
        if prior:
            return {"device_id": device_id, "business_day": business_day, "sealed_at": prior[0],
                    "snapshot_sha256": json.loads(prior[1])["sha256"], "idempotent": True}
        confirmed = [r for r in ledger["receivables"]["confirmed_events"] if r["business_day"] == business_day]
        snapshot = {"device_id": device_id, "business_day": business_day, "contiguous_seq": ledger["contiguous_seq"],
                    "watermark_version": ledger["version"], "confirmed_events": confirmed,
                    "sha256": hashlib.sha256(json.dumps(confirmed, ensure_ascii=False, sort_keys=True,
                                                        default=str).encode()).hexdigest(),
                    "sealed_at": _now()}
        db.execute("INSERT INTO day_settlements(device_id,business_day,sealed_at,snapshot) VALUES(?,?,?,?)",
                   (device_id, business_day, snapshot["sealed_at"], json.dumps(snapshot, ensure_ascii=False)))
    return {"device_id": device_id, "business_day": business_day, "sealed_at": snapshot["sealed_at"],
            "snapshot_sha256": snapshot["sha256"], "idempotent": False}
def archive_ledger(device_id):
    import csv, io
    ledger = get_ledger(device_id)
    stream = io.StringIO(newline=""); writer = csv.writer(stream)
    writer.writerow(["device_id", "watermark_version", "seq", "segment_state", "duplicates", "checksum",
                     "business_days", "received_at", "gap_ranges"])
    gap_text = ";".join(f"{a}-{b}" if a != b else str(a) for a, b in ledger["gap_ranges"])
    for seg in ledger["segments"]:
        state = "连续区段" if seg["seq"] <= ledger["contiguous_seq"] else "待补段"
        writer.writerow([device_id, ledger["version"], seg["seq"], state, seg["duplicates"],
                         seg["checksum"], seg["business_days"], seg["received_at"], gap_text])
    writer.writerow([])
    writer.writerow(["business_day", "confirmed_total", "pending_exposure", "pending_events", "status", "sealed"])
    for day in ledger["receivables"]["days"]:
        writer.writerow([day["business_day"], day["confirmed_total"], day["pending_exposure"],
                         day["pending_events"], day["status"], day["sealed"]])
    return "\ufeff" + stream.getvalue()
def reset_demo():
    ensure_schema()
    with connect() as db:
        db.execute("DELETE FROM device_watermarks")
        db.execute("DELETE FROM device_segments")
        db.execute("DELETE FROM day_settlements")
