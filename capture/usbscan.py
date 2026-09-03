#!/usr/bin/env python3
"""Scan a USBPcap .pcap/.pcapng: per (bus,device) packet counts, endpoints, VID:PID from device descriptors."""
import sys, struct, collections
def pcap_pkts(f):
    magic = f.read(4)
    if magic in (b'\xd4\xc3\xb2\xa1', b'\xa1\xb2\xc3\xd4'):
        le = magic == b'\xd4\xc3\xb2\xa1'; e = '<' if le else '>'
        hdr = f.read(20); lt = struct.unpack(e+'I', hdr[16:20])[0]
        while True:
            h = f.read(16)
            if len(h) < 16: return
            ts, tu, cl, ol = struct.unpack(e+'IIII', h); yield lt, ts+tu/1e6, f.read(cl)
    elif magic == b'\x0a\x0d\x0d\x0a':
        f.seek(0); lts = []
        while True:
            h = f.read(8)
            if len(h) < 8: return
            bt, bl = struct.unpack('<II', h)
            if bt == 0x0a0d0d0a:
                body = f.read(bl-8); bo = body[:4]; e = '<' if bo == b'\x4d\x3c\x2b\x1a' else '>'
                bt, bl = struct.unpack(e+'II', h); continue
            body = f.read(bl-8)
            if bt == 1: lts.append(struct.unpack(e+'H', body[:2])[0])
            elif bt == 6:
                iid, th, tl, cl, ol = struct.unpack(e+'IIIII', body[:20]); yield lts[iid], ((th<<32)|tl)/1e6, body[20:20+cl]
    else: raise SystemExit('unknown magic %r' % magic)
stats = collections.defaultdict(lambda: {'n':0,'bytes':0,'eps':collections.Counter(),'ids':set(),'first':None,'last':None})
for lt, ts, d in pcap_pkts(open(sys.argv[1],'rb')):
    if lt != 249 or len(d) < 27: continue
    hl, irp, st, fn, info, bus, dev, ep, tt, dl = struct.unpack('<HQIHBHHBBI', d[:27])
    s = stats[(bus,dev)]; s['n'] += 1; s['bytes'] += dl; s['eps'][(ep, tt)] += 1
    s['first'] = s['first'] or ts; s['last'] = ts
    p = d[hl:]
    for off in (0, 1):  # control payloads may be preceded by a stage byte
        if len(p) >= off+18 and p[off] == 0x12 and p[off+1] == 0x01:
            s['ids'].add('%04x:%04x' % struct.unpack('<HH', p[off+8:off+12]))
for (bus,dev), s in sorted(stats.items()):
    print(f'bus {bus} dev {dev:3d}: {s["n"]:8d} pkts {s["bytes"]:11d} B  {s["first"] and round(s["last"]-s["first"])}s  ids={sorted(s["ids"])}  eps={dict(s["eps"].most_common(6))}')
