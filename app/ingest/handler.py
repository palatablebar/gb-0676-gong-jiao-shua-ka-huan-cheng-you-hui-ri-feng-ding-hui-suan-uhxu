from flask import Blueprint, request, jsonify
from app.errors import DomainError
from app.ingest import service, demo
bp = Blueprint("ingest", __name__)
@bp.get("/api/ingest/devices")
def devices():
    return jsonify({"devices": service.list_devices()})
@bp.get("/api/ingest/devices/<device_id>")
def ledger(device_id):
    return jsonify(service.get_ledger(device_id))
@bp.post("/api/ingest/devices/<device_id>/segments/<int:seq>")
def upload(device_id, seq):
    p = request.get_json(silent=True)
    if not isinstance(p, dict) or "payload" not in p:
        raise DomainError("BAD_BODY", "请求需要含 payload 的对象")
    return jsonify(service.ingest_segment(device_id, seq, p["payload"],
                                          p.get("checksum"), p.get("expected_version")))
@bp.post("/api/ingest/devices/<device_id>/seal/<business_day>")
def seal(device_id, business_day):
    return jsonify(service.seal_day(device_id, business_day))
@bp.get("/api/ingest/devices/<device_id>/archive")
def archive(device_id):
    from flask import current_app
    return current_app.response_class(service.archive_ledger(device_id), mimetype="text/csv",
                                      headers={"Content-Disposition": f'attachment; filename="watermark-{device_id}.csv"'})
@bp.post("/api/ingest/demo/seed")
def seed():
    return jsonify({"seeded": demo.seed_demo()})
@bp.post("/api/ingest/demo/fill-gap")
def fill_gap():
    return jsonify(demo.seed_gap_fill())
@bp.post("/api/ingest/demo/reset")
def reset():
    service.reset_demo()
    demo.seed_demo()
    return jsonify({"reset": True})
