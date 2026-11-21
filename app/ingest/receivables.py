from datetime import timezone, timedelta
from app.modules.m2_routes.rules import evaluate as stage2
from app.modules.m3_fares.rules import evaluate as stage3
from app.modules.m4_journeys.rules import evaluate as stage4
from app.modules.m5_discounts.rules import evaluate as stage5
from app.reasons import reject
from app.values import instant
CN8 = timezone(timedelta(hours=8))
def business_day_of(event):
    at = event.get("event_time")
    try:
        return instant(at).astimezone(CN8).date().isoformat()
    except Exception:
        return "未知（待核验）"
def _builders(items, status, source):
    rows, quar = [], []
    for seq, seg in items:
        events = seg.get("events") or []
        for idx, event in enumerate(events, 1):
            if not isinstance(event, dict):
                quar.append({"_line": 0, "reason": "BAD_EVENT", "detail": "段内事件不是对象",
                             "event_id": "", "card": "", "_tag": {"seg": seq, "status": status, "source": source}})
                continue
            row = {**event, "_line": 0, "_sources": [f"seg{seq}#e{idx}"],
                   "_tag": {"seg": seq, "status": status, "source": source}}
            if not str(event.get("event_id", "")).strip():
                quar.append(reject(row, "EMPTY_VALUE", "业务键为空"))
                continue
            rows.append(row)
    return rows, quar
def recompute(promoted_segments, pending_segments):
    """连续段走 m2..m5 定稿；待补段只按基础票价计敞口，不参与换乘/封顶。"""
    promoted_rows, promoted_bad = _builders(
        [(s["seq"], s["payload"]) for s in promoted_segments], "continuous", "连续区段")
    pending_rows, pending_bad = _builders(
        [(s["seq"], s["payload"]) for s in pending_segments], "pending", "待补段")
    seen, dedup_bad = set(), []
    unique = []
    for row in promoted_rows:
        if row["event_id"] in seen:
            dedup_bad.append(reject(row, "DUPLICATE_CONFLICT", "跨段重复事件，仅保留先到段"))
            continue
        seen.add(row["event_id"]); unique.append(row)
    ctx = {"parameters": {}, "quarantine": []}
    r2 = stage2(unique, ctx); ctx["quarantine"].extend(r2["quarantine"])
    r3 = stage3(r2["rows"], ctx); ctx["quarantine"].extend(r3["quarantine"])
    r4 = stage4(r3["rows"], ctx); ctx["quarantine"].extend(r4["quarantine"])
    r5 = stage5(r4["rows"], ctx)
    confirmed = [{"card": r["card"], "business_day": r["business_day"], "event_id": r["event_id"],
                  "route": r["route"], "base_fare": r["base_fare"], "expected": r["expected"],
                  "transfer_discount": r["transfer_discount"], "cap_discount": r["cap_discount"],
                  "status": "连续口径已定稿", "seg": r["_tag"]["seg"]} for r in r5["rows"]]
    confirmed_bad = [{"event_id": q.get("event_id", ""), "card": q.get("card", ""),
                      "reason": q.get("reason", ""), "detail": q.get("detail", ""),
                      "seg": q.get("_tag", {}).get("seg")} for q in ctx["quarantine"]]
    pending_events, pending_quar = [], list(pending_bad)
    for row in pending_rows:
        day = business_day_of(row)
        try:
            at = instant(row["event_time"]).isoformat()
            from app.modules.m3_fares.rules import select_fare, FARES
            rule = select_fare(instant(row["event_time"]), FARES)
            reported = row.get("reported", "")
            pending_events.append({"event_id": row["event_id"], "card": row["card"],
                                   "business_day": day, "at": at,
                                   "base_fare": rule["amount"], "reported": str(reported),
                                   "seg": row["_tag"]["seg"], "status": "待补未定稿"})
        except Exception as exc:
            pending_quar.append({"event_id": row.get("event_id", ""), "card": row.get("card", ""),
                                 "reason": getattr(exc, "code", "INVALID_TIME"),
                                 "detail": getattr(exc, "message", str(exc)),
                                 "seg": row["_tag"]["seg"]})
    days = {}
    for r in confirmed:
        d = days.setdefault(r["business_day"], {"business_day": r["business_day"], "confirmed_total": "0.00",
                                                 "confirmed_cards": set(), "pending_exposure": "0.00",
                                                 "pending_cards": set(), "pending_events": 0})
        from decimal import Decimal
        d["confirmed_total"] = str(Decimal(d["confirmed_total"]) + Decimal(r["expected"]))
        d["confirmed_cards"].add(r["card"])
    for r in pending_events:
        d = days.setdefault(r["business_day"], {"business_day": r["business_day"], "confirmed_total": "0.00",
                                                 "confirmed_cards": set(), "pending_exposure": "0.00",
                                                 "pending_cards": set(), "pending_events": 0})
        from decimal import Decimal
        d["pending_exposure"] = str(Decimal(d["pending_exposure"]) + Decimal(r["base_fare"]))
        d["pending_cards"].add(r["card"]); d["pending_events"] += 1
    for d in days.values():
        d["confirmed_cards"] = sorted(d["confirmed_cards"]); d["pending_cards"] = sorted(d["pending_cards"])
        d["settleable"] = d["pending_events"] == 0
        d["status"] = "可封存" if d["settleable"] else "缺口未核清·暂缓封存"
    from decimal import Decimal
    return {"confirmed_events": confirmed, "confirmed_quarantine": confirmed_bad + [
                {"event_id": q.get("event_id", ""), "card": q.get("card", ""),
                 "reason": q.get("reason", ""), "detail": q.get("detail", ""),
                 "seg": q.get("_tag", {}).get("seg")} for q in dedup_bad],
            "pending_events": pending_events, "pending_quarantine": pending_quar,
            "days": sorted(days.values(), key=lambda d: d["business_day"]),
            "metrics": {"confirmed_total": str(sum((Decimal(r["expected"]) for r in confirmed), Decimal(0))),
                        "pending_exposure": str(sum((Decimal(r["base_fare"]) for r in pending_events), Decimal(0))),
                        "confirmed_events": len(confirmed),
                        "quarantined_events": len(confirmed_bad) + len(dedup_bad) + len(pending_quar),
                        "pending_events": len(pending_events),
                        "transfer_hits": sum(1 for r in confirmed if r["transfer_discount"] != "0.00"),
                        "cap_hits": sum(1 for r in confirmed if r["cap_discount"] != "0.00")}}
