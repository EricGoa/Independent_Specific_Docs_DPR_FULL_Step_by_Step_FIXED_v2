#!/usr/bin/env python3
"""Read-only entry point for the pinned, unmodified upstream OCFL validator."""
import runpy
import sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/'vendor/pairtree'))
sys.path.insert(0,str(root/'vendor/ocfl-py'))
sys.path.insert(0,str(root/'.validation-deps'))
def windows_path_adapter():
    # Upstream currently joins virtual OCFL paths with ntpath on Windows,
    # although fsspec returns POSIX paths. Adapt joins within ocfl modules only;
    # upstream validation rules and digest checks remain unmodified.
    import os, posixpath, types, ocfl
    if os.name != 'nt': return
    path_proxy=types.SimpleNamespace(**{name:getattr(os.path,name) for name in dir(os.path) if not name.startswith('__')})
    path_proxy.join=lambda *parts:os.path.join(*(str(p) for p in parts)).replace('\\','/')
    path_proxy.relpath=lambda path,start='.':os.path.relpath(path,start).replace('\\','/')
    os_proxy=types.SimpleNamespace(**{name:getattr(os,name) for name in dir(os) if not name.startswith('__')})
    os_proxy.path=path_proxy
    for name,module in list(sys.modules.items()):
        if name.startswith('ocfl.') and getattr(module,'os',None) is os: module.os=os_proxy

if __name__=='__main__':
    windows_path_adapter()
    runpy.run_path(str(root/'vendor/ocfl-py/ocfl-validate.py'),run_name='__main__')
