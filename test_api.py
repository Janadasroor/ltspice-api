import sys, os, json, urllib.request, time

BASE = "http://127.0.0.1:8000"

def req(method, path, data=None):
    url = f"{BASE}{path}"
    if data is not None:
        body = json.dumps(data).encode()
        r = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        r.method = method
    else:
        r = urllib.request.Request(url)
        r.method = method
    resp = urllib.request.urlopen(r)
    return resp.status, json.loads(resp.read().decode())

def req_text(method, path, data=None):
    url = f"{BASE}{path}"
    if data is not None:
        body = json.dumps(data).encode()
        r = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        r.method = method
    else:
        r = urllib.request.Request(url)
        r.method = method
    resp = urllib.request.urlopen(r)
    return resp.status, resp.read().decode()

# 1. Health
status, data = req("GET", "/health")
assert status == 200, f"health failed: {status}"
print(f"[OK] /health: {data}")

# 2. Simulate raw netlist
status, data = req("POST", "/simulate", {
    "netlist": "Test RC\nV1 in 0 SINE(0 1 1k)\nR1 in out 1k\nC1 out 0 100n\n.tran 1m\n"
})
assert status == 200, f"simulate failed: {status}"
sim_id = data["id"]
print(f"[OK] /simulate id={sim_id} status={data['status']}")

# 3. Get summary
status, text = req_text("GET", f"/results/{sim_id}/summary")
assert status == 200
assert "V(out)" in text
print(f"[OK] /results/{sim_id}/summary (contains V(out))")

# 4. Get results
status, data = req("GET", f"/results/{sim_id}")
assert status == 200
assert data["successful"] == True
assert "measurements" in data
print(f"[OK] /results/{sim_id}: successful={data['successful']} vars={data['num_variables']} pts={data['num_points']}")

# 5. Get data for V(out)
status, data = req("GET", f"/results/{sim_id}/data?var=V(out)")
assert status == 200
assert len(data["data"]) > 0
print(f"[OK] /data?var=V(out): {len(data['data'])} points, peak={max(abs(v) for v in data['data']):.4f}")

# 6. Get FFT
status, data = req("GET", f"/results/{sim_id}/fft?var=V(out)")
assert status == 200
assert len(data["freq"]) > 0
print(f"[OK] /fft?var=V(out): {len(data['freq'])} bins, fs={data['fs']:.0f}")

# 7. Get measurements
status, data = req("GET", f"/results/{sim_id}/measurements")
assert status == 200
print(f"[OK] /measurements: {data['measurements']}")

# 8. Get log
status, text = req_text("GET", f"/results/{sim_id}/log")
assert status == 200
assert len(text) > 0
print(f"[OK] /log ({len(text)} chars)")

# 9. Get netlist
status, text = req_text("GET", f"/results/{sim_id}/netlist")
assert status == 200
assert ".tran 1m" in text
print(f"[OK] /netlist")

# 10. Build and simulate circuit via JSON
status, data = req("POST", "/circuit", {
    "title": "RCDivider",
    "components": [
        {"type": "V", "name": "V1", "nodes": ["in", "0"], "value": "SINE(0 1 1k)"},
        {"type": "R", "name": "R1", "nodes": ["in", "out"], "value": "1k"},
        {"type": "C", "name": "C1", "nodes": ["out", "0"], "value": "100n"},
    ],
    "analysis": "tran 2m",
})
assert status == 200
cid = data["id"]
print(f"[OK] /circuit id={cid} status={data['status']}")

status, data = req("GET", f"/results/{cid}")
assert data["successful"] == True
print(f"[OK] circuit result: {data['num_points']} pts")

# 11. Build netlist (without sim)
status, text = req_text("POST", "/netlist", {
    "components": [
        {"type": "V", "name": "V1", "nodes": ["in", "0"], "value": "DC 5"},
        {"type": "R", "name": "R1", "nodes": ["in", "out"], "value": "1k"},
        {"type": "R", "name": "R2", "nodes": ["out", "0"], "value": "2k"},
    ],
})
assert status == 200
assert "V1 in 0 DC 5" in text and "R1 in out 1k" in text
print(f"[OK] /netlist: generated {len(text)} chars")

# 12. Delete
status, data = req("DELETE", f"/results/{sim_id}")
assert status == 200
print(f"[OK] DELETE /results/{sim_id}")

print("\n=== ALL TESTS PASSED ===")
