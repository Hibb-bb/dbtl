import json

import pytest

from harness import api
from harness.config import load_campaign
from harness.errors import HarnessError
from harness.ledger import ChainBroken, Ledger
from harness.schemas import validate

from conftest import write_yaml


def test_config_rejects_unknown_key(tmp_path):
    r = write_yaml(tmp_path / "c", surprise=1)
    with pytest.raises(HarnessError) as ex:
        load_campaign(r)
    assert ex.value.rule == "config" and "surprise" in ex.value.message


def test_config_rejects_unknown_nested_key(tmp_path):
    r = write_yaml(tmp_path / "c", hypotheses={"max_live": 3})
    with pytest.raises(HarnessError) as ex:
        load_campaign(r)
    assert "max_live" in ex.value.message


def test_config_rejects_bad_backend(tmp_path):
    r = write_yaml(tmp_path / "c", backend="v9")
    with pytest.raises(HarnessError):
        load_campaign(r)


def test_ledger_chain_links(root):
    led = Ledger(root / "ledger")
    es = led.entries()
    assert es[0]["prev"] == "0" * 64
    for a, b in zip(es, es[1:]):
        assert b["prev"] == a["hash"]
        assert b["seq"] == a["seq"] + 1
    assert led.verify()[0]


def test_hand_edit_in_middle_breaks_chain(root):  # acceptance 18
    path = root / "ledger" / "chain.jsonl"
    lines = path.read_text().splitlines()
    mid = len(lines) // 2
    e = json.loads(lines[mid])
    e["body"]["values"]["activity"] = 0.999  # tamper with an observation
    lines[mid] = json.dumps(e, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")
    led = Ledger(root / "ledger")
    ok, msg = led.verify()
    assert not ok and f"line {mid + 1}" in msg
    with pytest.raises(ChainBroken):
        led.append("rejection", {"x": 1}, 0)  # refuses to extend a broken chain
    with pytest.raises(ChainBroken):
        api.hypothesize(root, [])


def test_rehashed_edit_still_breaks_chain(root):
    """Editing a line AND recomputing its hash breaks the next line's prev link."""
    from harness.ledger import line_hash
    path = root / "ledger" / "chain.jsonl"
    lines = path.read_text().splitlines()
    e = json.loads(lines[3]); e["body"]["values"]["activity"] = 0.1; e["hash"] = line_hash(e)
    lines[3] = json.dumps(e, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")
    ok, msg = Ledger(root / "ledger").verify()
    assert not ok and "line 5" in msg


def test_deleted_line_breaks_chain(root):
    path = root / "ledger" / "chain.jsonl"
    lines = path.read_text().splitlines()
    del lines[2]
    path.write_text("\n".join(lines) + "\n")
    assert not Ledger(root / "ledger").verify()[0]


def test_raw_responses_stored_and_hashed(root):
    led = Ledger(root / "ledger")
    res = [e for e in led.entries() if e["kind"] == "result"][0]["body"]
    raw = root / res["raw_path"]
    assert raw.exists()
    from harness.ledger import sha256_file
    assert sha256_file(raw) == res["raw_hash"]


def test_schemas_reject_extra_fields():
    with pytest.raises(HarnessError):
        validate("batch_plan", {"round": 1, "picks": [], "notes": "x"})


def test_fake_adapter_is_deterministic(tmp_path):
    r1 = write_yaml(tmp_path / "a"); r2 = write_yaml(tmp_path / "b")
    a1 = api._adapter(load_campaign(r1)); a2 = api._adapter(load_campaign(r2))
    assert a1.pool() == a2.pool() and a1.load_start() == a2.load_start()
    a1.submit(1, ["C-050"]); a2.submit(1, ["C-050"])
    assert a1.collect("fake-r1") == a2.collect("fake-r1")


def test_fake_adapter_queue_and_failures(tmp_path):
    r = write_yaml(tmp_path / "a", backend_options={"collect_delay": 2, "fail_rate": 0.5, "pool_size": 40, "start_size": 4})
    a = api._adapter(load_campaign(r))
    h = a.submit(1, [c["id"] for c in a.pool()][:20])
    assert a.collect(h) is None and a.collect(h) is None
    res = a.collect(h)
    assert res is not None
    fails = [m for m in res["measurements"] if m["failed"]]
    assert fails and all(m["values"] == {} for m in fails)
