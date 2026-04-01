#!/usr/bin/env python3
"""
Patch script to fix vLLM WebSocket KeyError: 'method' bug.

This script modifies the vLLM server_utils.py file to properly handle
ASGI WebSocket scopes which don't have a 'method' key.

The bug is in: /usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/server_utils.py
Line 72: if scope["type"] not in ("http", "websocket") or scope["method"] == "OPTIONS":

The fix changes scope["method"] to scope.get("method") to safely handle WebSocket connections.
"""

import os
import re
import sys

VLLM_SERVER_UTILS_PATH = "/usr/local/lib/python3.12/dist-packages/vllm/entrypoints/openai/server_utils.py"

def patch_vllm_server_utils():
    """Patch the vLLM server_utils.py file to fix the WebSocket KeyError."""
    
    if not os.path.exists(VLLM_SERVER_UTILS_PATH):
        print(f"Error: vLLM server_utils.py not found at {VLLM_SERVER_UTILS_PATH}")
        print("This may be expected if vLLM is not installed yet.")
        return False
    
    with open(VLLM_SERVER_UTILS_PATH, 'r') as f:
        content = f.read()
    
    # The buggy line pattern
    buggy_pattern = r'scope\["method"\]\s*==\s*"OPTIONS"'
    
    # Check if the bug exists
    if not re.search(buggy_pattern, content):
        print("vLLM server_utils.py appears to be already patched or has different code structure.")
        # Check if already patched
        if 'scope.get("method")' in content or "scope.get('method')" in content:
            print("Found scope.get('method') - file is already patched.")
            return True
        print("Could not find the expected pattern to patch.")
        return False
    
    # Apply the fix: change scope["method"] to scope.get("method")
    fixed_content = re.sub(
        r'scope\["method"\]\s*==\s*"OPTIONS"',
        'scope.get("method") == "OPTIONS"',
        content
    )
    
    with open(VLLM_SERVER_UTILS_PATH, 'w') as f:
        f.write(fixed_content)
    
    print(f"Successfully patched {VLLM_SERVER_UTILS_PATH}")
    print("Changed: scope[\"method\"] == \"OPTIONS\"")
    print("To:      scope.get(\"method\") == \"OPTIONS\"")
    return True

if __name__ == "__main__":
    success = patch_vllm_server_utils()
    sys.exit(0 if success else 1)
