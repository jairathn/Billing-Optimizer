"""Download helper for an agent fetching Dropbox files through the Dropbox connector's single-use links, without the agent
re-typing any listing or URL: it reads the agent's own transcript (found by a marker the agent echoed).
  init  <state> <marker>          find the agent's transcript (the subagent .jsonl holding the marker) and remember it
  build <state> <dest_root>       turn every list_folder result in the transcript into the work list (id, size, path)
  cycle <state>                   fetch the newest unused download_link result once (curl), check size and Dropbox's
                                  content hash, record what is done, print the next 25 ids to ask links for
Prints counts and ids only (never a file name or content)."""
import hashlib, json, os, re, subprocess, sys, time
ROOT = os.environ["RW_TRANSCRIPTS"]     # the session's transcripts folder: ~/.claude/projects/<project>/<session id>
NS = {"ns:15011251635//": "Trizetto Data/", "ns:15364949075//": ""}
mode, st = sys.argv[1], sys.argv[2]
os.makedirs(st, mode=0o700, exist_ok=True)

def jsons_in_line(line):
    """Every JSON object string in a transcript line, and the bodies of tool-results files it points at."""
    out = []
    def walk(o):
        if isinstance(o, str):
            s = o.lstrip()
            if s.startswith("{") and ('"entries"' in s):
                try: out.append(json.loads(s))
                except Exception: pass
            for p in re.findall(r"(/root/\.claude/projects/[^\s\"']*/tool-results/[A-Za-z0-9_.-]+)", o):
                try:
                    body = open(p).read()
                    if body.lstrip().startswith("{") and '"entries"' in body: out.append(json.loads(body))
                except Exception: pass
        elif isinstance(o, list):
            for x in o: walk(x)
        elif isinstance(o, dict):
            for x in o.values(): walk(x)
    try: walk(json.loads(line))
    except Exception: pass
    return out

def results(trn):
    for line in open(trn):
        if '"entries' not in line and 'tool-results/' not in line: continue
        for j in jsons_in_line(line): yield j

def rd(f):
    try: return [l for l in open(os.path.join(st, f)).read().split("\n") if l]
    except FileNotFoundError: return []

if mode == "init":
    marker = sys.argv[3]
    hits = []
    for d, _, fs in os.walk(ROOT + "/subagents"):
        for f in fs:
            if f.endswith(".jsonl"):
                p = os.path.join(d, f)
                try:
                    if marker in open(p, errors="ignore").read(): hits.append(p)
                except Exception: pass
    if len(hits) != 1:
        print("transcript not found uniquely:", len(hits)); sys.exit(1)
    open(os.path.join(st, "trn"), "w").write(hits[0])
    print("transcript found")
    sys.exit(0)

trn = open(os.path.join(st, "trn")).read().strip()
if mode == "build":
    dest = sys.argv[3]
    work = {}
    for j in results(trn):
        for e in j.get("entries", []):
            if e.get("object_type") != "file" or "download_url" in e: continue
            path, fid = e.get("path") or "", e.get("file_id") or e.get("id")
            if not fid or ".nosync" in path: continue
            for k, v in NS.items():
                if path.startswith(k): rel = v + path[len(k):]; break
            else: continue
            size = (e.get("file") or {}).get("size", e.get("size", 0)) or 0
            work[fid] = (int(size), os.path.join(dest, rel))
    # a slice k/n of the folders' files (sorted by id), so several agents share one folder without overlap
    if len(sys.argv) > 4:
        k, n = (int(x) for x in sys.argv[4].split("/"))
        work = {fid: work[fid] for i, fid in enumerate(sorted(work)) if i % n == k}
    # files an earlier agent already fetched and checked (on disk at the listed size) are done
    with open(os.path.join(st, "done"), "a") as f:
        for fid, (s, p) in work.items():
            if s > 0 and os.path.isfile(p) and os.path.getsize(p) == s:
                f.write(fid + "\n")
    with open(os.path.join(st, "work.tsv"), "w") as f:
        for fid, (s, p) in work.items(): f.write(f"{fid}\t{s}\t{p}\n")
    by = {}
    for s, p in work.values():
        k = os.path.dirname(p).split("Trizetto Data/")[-1]
        by.setdefault(k, [0, 0]); by[k][0] += 1; by[k][1] += s
    print("work list:", len(work), "files,", sum(s for s, _ in work.values()), "bytes;", len(set(rd("done"))), "already on disk")
    for k, (n, b) in sorted(by.items()): print(f"  {k}: {n} files, {b} bytes")
    sys.exit(0)

def dbx_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(4 * 1024 * 1024)
            if not b: break
            h.update(hashlib.sha256(b).digest())
    return h.hexdigest()

work, order = {}, []
for l in rd("work.tsv"):
    i, s, p = l.split("\t"); work[i] = (int(s), p); order.append(i)
done, used = set(rd("done")), set(rd("used"))
for i in order:                                     # already on disk with the right size (an earlier run): done
    if i not in done and os.path.isfile(work[i][1]) and os.path.getsize(work[i][1]) == work[i][0] and work[i][0] > 0:
        pass
if mode == "cycle":
    last = None
    for j in results(trn):
        if j.get("entries") and "download_url" in (j["entries"][0] or {}): last = j
    ents = [e for e in (last or {}).get("entries", []) if hashlib.sha256(e["download_url"].encode()).hexdigest() not in used]
    todo = [e for e in ents if e.get("id") in work and e["id"] not in done]
    with open(os.path.join(st, "used"), "a") as f:
        for e in ents: f.write(hashlib.sha256(e["download_url"].encode()).hexdigest() + "\n")
    if todo:
        cfg = os.path.join(st, "curl.cfg")
        with open(cfg, "w") as c:
            os.chmod(cfg, 0o600)
            for e in todo:
                p = work[e["id"]][1]
                os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
                esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
                c.write('url = "%s"\noutput = "%s"\n' % (esc(e["download_url"]), esc(p)))
        r = subprocess.run(["curl", "-sS", "--fail", "--parallel", "--parallel-max", "25", "-K", cfg], stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True)
        os.remove(cfg)
        ok = bad = 0
        for e in todo:
            sz, p = work[e["id"]]
            good = os.path.isfile(p) and os.path.getsize(p) == sz and (not e.get("content_hash") or dbx_hash(p) == e["content_hash"])
            if good:
                done.add(e["id"]); ok += 1
                with open(os.path.join(st, "done"), "a") as f: f.write(e["id"] + "\n")
            else:
                bad += 1
                if os.path.exists(p): os.remove(p)
        print("batch ok", ok, "failed", bad, "curl rc", r.returncode)
    pend = [i for i in order if i not in done]
    print("done", len(done), "of", len(work), "pending", len(pend))
    print(json.dumps(pend[:25]))
