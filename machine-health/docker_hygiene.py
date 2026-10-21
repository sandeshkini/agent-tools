"""docker-hygiene (a machine-health check): REPORT ONLY. Lists images that could go; never deletes anything.
Sandesh decides. lifeos-rollback:* (and every other *-rollback* tag) is never listed as a candidate.

Candidates: dangling images; cos-hub tags beyond the newest 3 (never `latest`/`previous`/rollback tags);
cosreltest*; lifeos-app and lifeos-web tags other than latest.
"""
import json, re, subprocess, time

import stuck_watch as sw

KEEP_COS_HUB = 3
PROTECTED = re.compile(r"rollback", re.I)


def human(n):
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def parse_size(s):
    m = re.match(r"([\d.]+)\s*(B|kB|KB|MB|GB|TB)", s or "")
    return float(m.group(1)) * {"B": 1, "kB": 1e3, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}[m.group(2)] if m else 0


def classify(images):
    """images: [{repo, tag, id, size (bytes), created (sortable str)}] -> {category: [image]} candidates only."""
    out = {"dangling": [], "cos-hub beyond newest %d" % KEEP_COS_HUB: [], "cosreltest": [], "lifeos-app/web old tags": []}
    keys = list(out)
    hub = sorted([i for i in images if i["repo"] == "cos-hub" and not PROTECTED.search(i["tag"])],
                 key=lambda i: i["created"], reverse=True)
    newest = []                                   # the newest 3 distinct images, whatever their tags
    for i in hub:
        if i["id"] not in newest and len(newest) < KEEP_COS_HUB:
            newest.append(i["id"])
    inuse_ids = set(newest) | {i["id"] for i in hub if i["tag"] in ("latest", "previous")}
    cos = [i for i in hub if i["id"] not in inuse_ids]
    for i in images:
        if PROTECTED.search(i["repo"] + ":" + i["tag"]):
            continue
        if i["repo"] == "<none>":
            out[keys[0]].append(i)
        elif i["repo"].startswith("cosreltest"):
            out[keys[2]].append(i)
        elif i["repo"] in ("lifeos-app", "lifeos-web") and i["tag"] != "latest":
            out[keys[3]].append(i)
    out[keys[1]] = cos
    return out


def _images(*args):
    r = subprocess.run(["docker", "images", *args, "--format", "{{json .}}"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:120] or "docker not answering")
    return [json.loads(l) for l in r.stdout.splitlines()]


def list_images():
    imgs, seen = [], set()
    for d in _images() + _images("-f", "dangling=true"):
        k = (d["Repository"], d["Tag"], d["ID"])
        if k not in seen:
            seen.add(k)
            imgs.append({"repo": d["Repository"], "tag": d["Tag"], "id": d["ID"], "size": parse_size(d["Size"]),
                         "created": d.get("CreatedAt", "")})
    return imgs


def reclaimable():
    """Docker's own figure for unused images (counts shared layers once)."""
    r = subprocess.run(["docker", "system", "df", "--format", "{{json .}}"], capture_output=True, text=True, timeout=60)
    for l in r.stdout.splitlines():
        d = json.loads(l)
        if d.get("Type") == "Images":
            return d.get("Reclaimable", "?"), d.get("TotalCount", "?")
    return "?", "?"


def report(imgs):
    """(summary line, detail lines). Reclaimable counts each image id once (tags share layers/ids)."""
    cands = classify(imgs)
    cand_keys = {(i["repo"], i["tag"], i["id"]) for v in cands.values() for i in v}
    shared = {i["id"] for i in imgs if (i["repo"], i["tag"], i["id"]) not in cand_keys}   # id also carries a kept tag
    lines, ids = [], {}
    for cat, items in cands.items():
        for i in items:
            ids[i["id"]] = i["size"]
        if items and cat == "dangling":
            lines.append(f"dangling: {len(items)} untagged images (sizes overlap: shared layers; see docker's figure above)")
        elif items:
            lines.append(f"{cat}: " + ", ".join(
                f"{i['repo']}:{i['tag']} {human(i['size'])}" + (" (same image as a kept tag: frees nothing)" if i["id"] in shared else "")
                for i in items))
    n = sum(len(v) for v in cands.values())
    rec, total = reclaimable()
    return (f"{n} candidate images; docker says {rec} of {total} images are unused (report only)", lines)


def run(dry=False, force=False):
    try:
        summary, lines = report(list_images())
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        return f"docker not available: {e}"
    sw.log("DOCKER", summary)
    for l in lines:
        sw.log("DOCKER", "  " + l)
    return summary
