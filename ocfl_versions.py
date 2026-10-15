"""Append-only OCFL forward deltas and focused inventory validation."""
import copy
import json
import os
import shutil
import uuid
from pathlib import Path
from workflow_common import digest, load, verify

def safe_path(root, relative):
    if not relative or "\\" in relative or ":" in relative or any(p in {"", ".", ".."} for p in relative.split("/")):
        raise RuntimeError(f"Unsafe inventory path: {relative}")
    p = root / relative
    p.resolve().relative_to(root.resolve())
    return p

def validate(object_root, objid):
    errors=[]
    try:
        if (object_root/"0=ocfl_object_1.1").read_text(encoding="utf-8") != "ocfl_object_1.1\n":
            raise RuntimeError("Invalid OCFL declaration")
        inv=load(object_root/"inventory.json")
        if inv["id"] != objid or inv["digestAlgorithm"] != "sha512" or inv["type"] != "https://ocfl.io/1.1/spec/#inventory":
            raise RuntimeError("OCFL inventory identity/type/algorithm mismatch")
        head=inv["head"]; versions=inv["versions"]; manifest=inv["manifest"]
        if set(versions) != {f"v{i}" for i in range(1,int(head[1:])+1)}:
            raise RuntimeError("Non-contiguous version history")
        if (object_root/"inventory.json").read_bytes() != (object_root/head/"inventory.json").read_bytes():
            raise RuntimeError("Root and head inventories differ")
        physical=set()
        for checksum, paths in manifest.items():
            if not paths: raise RuntimeError("Empty manifest path list")
            for rel in paths:
                if rel in physical: raise RuntimeError("Duplicate physical path")
                physical.add(rel); verify(safe_path(object_root,rel),checksum)
        actual={p.relative_to(object_root).as_posix() for version in versions for p in (object_root/version/"content").rglob("*") if p.is_file()}
        if physical != actual: raise RuntimeError("Physical content differs from manifest")
        used=set()
        for version, data in versions.items():
            paths=set()
            for checksum, logicals in data["state"].items():
                if checksum not in manifest or not logicals: raise RuntimeError("State digest missing from manifest")
                used.add(checksum)
                for logical in logicals:
                    safe_path(object_root,logical)
                    if logical in paths: raise RuntimeError("Duplicate logical path")
                    paths.add(logical)
            old=load(object_root/version/"inventory.json")
            if old["head"] != version or old["id"] != objid or old["versions"] != {k:v for k,v in versions.items() if int(k[1:]) <= int(version[1:])}:
                raise RuntimeError(f"Historical inventory mismatch: {version}")
            for checksum, rels in old["manifest"].items():
                if not set(rels) <= set(manifest.get(checksum,[])): raise RuntimeError("Historical manifest lost")
        if used != set(manifest): raise RuntimeError("Unused manifest digest")
        for parent in [object_root]+[object_root/v for v in versions]:
            side=(parent/"inventory.json.sha512").read_text(encoding="utf-8").split()
            if len(side)!=2 or side[1]!="inventory.json": raise RuntimeError("Malformed inventory sidecar")
            verify(parent/"inventory.json",side[0])
    except Exception as exc:
        errors.append(str(exc))
    return errors

def build_delta(bag, entries, objid, existing, workspace, user, address, timestamp):
    problems=validate(existing,objid)
    if problems: raise RuntimeError("Existing OCFL object invalid: "+"; ".join(problems))
    old=load(existing/"inventory.json")
    inv=copy.deepcopy(old)
    old_state={p:d for d,paths in old["versions"][old["head"]]["state"].items() for p in paths}
    incoming={p.removeprefix("data/"):d for p,d in entries.items()}
    # Incremental packages cannot silently remove earlier files.
    merged=old_state | incoming
    if merged == old_state: return old, []
    shutil.copytree(existing,workspace)
    head=f"v{int(old['head'][1:])+1}"
    content=workspace/head/"content";content.mkdir(parents=True)
    copies=[]
    for logical, checksum in sorted(incoming.items()):
        if checksum in inv["manifest"]: continue
        target=safe_path(content,logical);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(safe_path(bag,"data/"+logical),target);verify(target,checksum)
        physical=f"{head}/content/{logical}"
        inv["manifest"][checksum]=[physical]
        copies.append({"logical_path":logical,"physical_path":physical,"bag_manifest_sha512":checksum,"ocfl_written_sha512":checksum,"verified":True,"size_bytes":target.stat().st_size})
    state={}
    for logical, checksum in sorted(merged.items()): state.setdefault(checksum,[]).append(logical)
    version={"created":timestamp,"message":"Incremental preservation ingest; previous logical paths retained","state":state,"user":{"name":user}}
    if address: version["user"]["address"]=address
    inv["head"]=head;inv["versions"][head]=version
    payload=json.dumps(inv,ensure_ascii=False,indent=2)+"\n"
    for parent in (workspace,workspace/head):
        (parent/"inventory.json").write_text(payload,encoding="utf-8",newline="\n")
        (parent/"inventory.json.sha512").write_text(digest(parent/"inventory.json")+" inventory.json\n",encoding="utf-8",newline="\n")
    return inv,copies

def commit_delta(workspace, final, previous_head):
    # Exclusive lock coordinates writers using this implementation. Interrupted
    # commits retain the workspace and version directory for manual recovery.
    lock=final/".ingest.lock"
    with lock.open("x",encoding="utf-8") as stream: stream.write(uuid.uuid4().hex)
    try:
        if load(final/"inventory.json")["head"] != previous_head:
            raise RuntimeError("OCFL head changed during ingest")
        inv=load(workspace/"inventory.json"); head=inv["head"]
        if (final/head).exists(): raise RuntimeError("Version already exists; recovery required")
        shutil.copytree(workspace/head,final/head)
        for name in ("inventory.json","inventory.json.sha512"):
            tmp=final/(name+".pending-"+uuid.uuid4().hex)
            shutil.copy2(workspace/name,tmp)
            os.replace(tmp,final/name)
    finally:
        lock.unlink()
