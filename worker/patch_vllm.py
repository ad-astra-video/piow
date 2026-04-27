#!/usr/bin/env python3
"""
Patch script to fix vLLM WebSocket KeyError: 'method' bug.

This script modifies the vLLM server_utils.py file to properly handle
ASGI WebSocket scopes which don't have a 'method' key.

The bug is in: /usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/server_utils.py
Line 72: if scope["type"] not in ("http", "websocket") or scope["method"] == "OPTIONS":

The fix changes scope["method"] to scope.get("method") to safely handle WebSocket connections.
"""

import glob
import os
import re
import sys

VLLM_SERVER_UTILS_CANDIDATES = [
    "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/server_utils.py",
    "/usr/local/lib/python3.11/dist-packages/vllm/entrypoints/openai/server_utils.py",
    "/usr/local/lib/python3.10/dist-packages/vllm/entrypoints/openai/server_utils.py",
]

def find_vllm_server_utils():
    """Search for vLLM server_utils.py in known and dynamic locations."""
    for path in VLLM_SERVER_UTILS_CANDIDATES:
        if os.path.exists(path):
            return path
    # Dynamic search under /usr/local/lib
    matches = glob.glob("/usr/local/lib/python3*/dist-packages/vllm/entrypoints/openai/server_utils.py")
    if matches:
        return matches[0]
    # Try site-packages (pip editable / venv installs)
    matches = glob.glob("/usr/local/lib/python3*/site-packages/vllm/entrypoints/openai/server_utils.py")
    if matches:
        return matches[0]
    return None

def patch_vllm_server_utils():
    """Patch the vLLM server_utils.py file to fix the WebSocket KeyError."""

    vllm_path = find_vllm_server_utils()
    if vllm_path is None:
        print("vLLM server_utils.py not found in any known location.")
        print("Skipping patch — vLLM may not be installed or the bug may already be fixed.")
        return True  # Not a fatal error; exit 0 so Docker build continues
    
    with open(vllm_path, 'r') as f:
        content = f.read()

    # The buggy line pattern
    buggy_pattern = r'scope\["method"\]\s*==\s*"OPTIONS"'

    # Check if the bug exists
    if not re.search(buggy_pattern, content):
        print("vLLM server_utils.py appears to be already patched or has different code structure.")
        if 'scope.get("method")' in content or "scope.get('method')" in content:
            print("Found scope.get('method') - file is already patched.")
            return True
        print("Could not find the expected pattern to patch. Skipping.")
        return True  # Not fatal — may be a different vLLM version

    # Apply the fix: change scope["method"] to scope.get("method")
    fixed_content = re.sub(
        r'scope\["method"\]\s*==\s*"OPTIONS"',
        'scope.get("method") == "OPTIONS"',
        content
    )

    with open(vllm_path, 'w') as f:
        f.write(fixed_content)

    print(f"Successfully patched {vllm_path}")
    print("Changed: scope[\"method\"] == \"OPTIONS\"")
    print("To:      scope.get(\"method\") == \"OPTIONS\"")
    return True

if __name__ == "__main__":
    success = patch_vllm_server_utils()
    sys.exit(0 if success else 1)
