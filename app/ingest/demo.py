from app.ingest.service import ingest_segment, checksum
def _event(eid, card, minute, route="01", reported="2.50", result="success", day="2026-11-16"):
    return {"event_id": eid, "card": card, "route": route, "result": result,
            "event_time": f"{day}T{8 + minute // 60:02d}:{minute % 60:02d}:00+08:00",
            "reported": reported, "note": "水位演示"}
def _seg(events):
    payload = {"events": events}
    return {"payload": payload, "checksum": checksum(payload)}
def demo_batches():
    """OB-101：1,2,4,5 先到（缺3，越序待补）；OB-102：1..3 连续，可封存。"""
    ob101 = {
        1: _seg([_event("D1001", "C101", 0, "01"), _event("D1002", "C101", 15, "02"), _event("D1003", "C101", 30, "03")]),
        2: _seg([_event("D1004", "C101", 45, "01"), _event("D1005", "C102", 5, "01")]),
        3: _seg([_event("D1006", "C101", 75, "02"), _event("D1007", "C102", 20, "02")]),
        4: _seg([_event("D1008", "C103", 10, "01"), _event("D1009", "C103", 25, "02")]),
        5: _seg([_event("D1010", "C104", 40, "03"), _event("D1011", "C104", 55, "01")]),
    }
    ob102 = {
        1: _seg([_event("D2001", "C201", 0, "01"), _event("D2002", "C201", 20, "02")]),
        2: _seg([_event("D2003", "C201", 40, "03"), _event("D2004", "C202", 10, "01")]),
        3: _seg([_event("D2005", "C202", 35, "02"), _event("D2006", "C202", 95, "01")]),
    }
    return {"OB-101": ob101, "OB-102": ob102}
def seed_demo():
    from app.ingest.service import list_devices
    if list_devices():
        return False
    batches = demo_batches()
    for seq in (1, 2, 4, 5):
        seg = batches["OB-101"][seq]
        ingest_segment("OB-101", seq, seg["payload"], seg["checksum"])
    for seq in (1, 2, 3):
        seg = batches["OB-102"][seq]
        ingest_segment("OB-102", seq, seg["payload"], seg["checksum"])
    return True
def seed_gap_fill():
    seg = demo_batches()["OB-101"][3]
    return ingest_segment("OB-101", 3, seg["payload"], seg["checksum"])
