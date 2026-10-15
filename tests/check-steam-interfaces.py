#!/usr/bin/env python3
"""Reject Enshrouded images lacking the game's required Steam interface.

A binary-string guard is a necessary packaging check, not a runtime join test.
"""
from pathlib import Path
import sys

paths = (
    Path('/usr/local/bin/files/lib/wine/x86_64-unix/lsteamclient.so'),
    Path('/home/enshrouded/.steam/sdk64/steamclient.so'),
)
failed = False
for path in paths:
    if not path.is_file():
        print(f'FAIL: missing {path}')
        failed = True
        continue
    supported = b'SteamUtils011' in path.read_bytes()
    print(f'{"PASS" if supported else "FAIL"}: {path}: SteamUtils011')
    failed |= not supported
sys.exit(1 if failed else 0)
