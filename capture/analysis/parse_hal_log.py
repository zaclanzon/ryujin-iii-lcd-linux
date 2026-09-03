"""Parse AacAIOFanHal S750 log: extract every HID W:/R: 64-byte dump with function name + timestamp."""
import re, sys, json, collections
src = sys.argv[1]; t0 = sys.argv[2] if len(sys.argv) > 2 else '00:00:00'; t1 = sys.argv[3] if len(sys.argv) > 3 else '23:59:59'
lines = open(src, encoding='utf-8', errors='replace').read().splitlines()
hdr = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+) \[AacAIOFanHal\]\[[\d.]+\]-\[(.*?)\] (.*)$')
recs = []; ctx = []
i = 0
while i < len(lines):
    m = hdr.match(lines[i])
    if not m: i += 1; continue
    ts, fn, msg = m.groups()
    t = ts[11:19]
    if t < t0 or t > t1: i += 1; continue
    dm = re.match(r'^(W|R): \[EC,\s*$', msg)
    if dm:
        hexs = []
        j = i + 1
        while j < len(lines) and len(hexs) < 63:
            hexs += re.findall(r'\b[0-9A-F]{2}\b', lines[j]); j += 1
        recs.append({'ts': ts, 'fn': fn.replace('S750::', ''), 'dir': dm.group(1), 'bytes': ['EC'] + hexs[:63], 'ctx': ctx[-3:]})
        i = j; continue
    # keep descriptive context lines (not the periodic noise)
    if not re.search(r'GlobalMutex|DoCommand\]|GetNumberOfSupported|WarningPlayer|UpdateValue|OledMonitor|doUpdate|GetProductLine|isMBSupport|CheckAsusMB|isSupportCpu', fn + msg):
        ctx.append(f'[{fn}] {msg}'[:160])
    i += 1
json.dump(recs, open('hid-records.json', 'w'), indent=0)
print(len(recs), 'records')
# timeline
with open('hid-timeline.txt', 'w', encoding='utf-8') as f:
    for r in recs:
        b = r['bytes']
        trimmed = ' '.join(b).rstrip(' 0').strip()
        f.write(f"{r['ts'][11:23]} {r['dir']} {r['fn']:<28} {trimmed}\n")
# unique write payloads per function (first 20 bytes) with example context
uniq = collections.OrderedDict()
for r in recs:
    if r['dir'] != 'W': continue
    key = (r['fn'], ' '.join(r['bytes']).rstrip(' 0').strip())
    if key not in uniq: uniq[key] = (r['ts'], r['ctx'], 1)
    else: uniq[key] = (uniq[key][0], uniq[key][1], uniq[key][2] + 1)
with open('hid-unique-writes.txt', 'w', encoding='utf-8') as f:
    for (fn, payload), (ts, ctx, n) in sorted(uniq.items(), key=lambda kv: (kv[0][1][3:5], kv[0][0])):
        f.write(f"## {fn}  (x{n}, first {ts[11:23]})\n   {payload}\n")
        for c in ctx: f.write(f"     ctx: {c}\n")
print(len(uniq), 'unique writes ->', 'hid-unique-writes.txt')
# responses per command byte
resp = collections.defaultdict(set)
for r in recs:
    if r['dir'] == 'R': resp[(r['fn'], r['bytes'][1])].add(' '.join(r['bytes']).rstrip(' 0').strip())
with open('hid-unique-reads.txt', 'w', encoding='utf-8') as f:
    for (fn, cmd), s in sorted(resp.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        f.write(f"## {fn} cmd {cmd}: {len(s)} distinct\n")
        for p in sorted(s)[:12]: f.write(f"   {p}\n")
