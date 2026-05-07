#!/usr/bin/env python3
"""Patch vLLM realtime endpoint to add timestamp_ms and SIL silence markers.

Changes:
  - protocol.py: Add timestamp_ms field to TranscriptionDelta
  - connection.py: Record elapsed ms per frame, emit "SIL" for delay tokens

Target: vllm v0.20.1
"""
import sys

PROTOCOL_PATH = "/usr/local/lib/python3.12/site-packages/vllm/entrypoints/openai/realtime/protocol.py"
CONN_PATH = "/usr/local/lib/python3.12/site-packages/vllm/entrypoints/openai/realtime/connection.py"


def patch_protocol():
    with open(PROTOCOL_PATH, "r") as f:
        src = f.read()

    old_delta_class = '''class TranscriptionDelta(OpenAIBaseModel):
    """Incremental transcription text"""

    type: Literal["transcription.delta"] = "transcription.delta"
    delta: str  # Incremental text'''

    new_delta_class = '''class TranscriptionDelta(OpenAIBaseModel):
    """Incremental transcription text with timing"""

    type: Literal["transcription.delta"] = "transcription.delta"
    delta: str             # Incremental text ("SIL" for silence/delay tokens)
    timestamp_ms: int = 0  # Milliseconds since generation started'''

    if old_delta_class not in src:
        print("ERROR: protocol.py TranscriptionDelta pattern not found", file=sys.stderr)
        sys.exit(1)

    src = src.replace(old_delta_class, new_delta_class)

    with open(PROTOCOL_PATH, "w") as f:
        f.write(src)
    print(f"Patched {PROTOCOL_PATH}")


def patch_connection():
    with open(CONN_PATH, "r") as f:
        src = f.read()

    # 0. Add 'import time' if missing
    if "import time" not in src:
        src = src.replace("import asyncio\n", "import asyncio\nimport time\n", 1)

    # 1. Insert start_ns after generate() call
    old_gen = '''            )

            # Stream results back to client as they're generated
            async for output in result_gen:'''

    new_gen = '''            )

            start_ns = time.monotonic_ns()

            # Stream results back to client as they're generated
            async for output in result_gen:'''

    if old_gen not in src:
        print("ERROR: connection.py pattern 1 not found", file=sys.stderr)
        sys.exit(1)

    # 2. Replace the delta / send block
    old_block = '''                    delta = output.outputs[0].text
                    full_text += delta

                    # append output to input
                    input_stream.put_nowait(list(output.outputs[0].token_ids))
                    await self.send(TranscriptionDelta(delta=delta))'''

    new_block = '''                    delta = output.outputs[0].text

                    # Emit "SIL" marker for delay tokens (empty text but non-empty token_ids)
                    if not delta and output.outputs[0].token_ids:
                        delta = "SIL"
                    else:
                        full_text += delta

                    # append output to input
                    input_stream.put_nowait(list(output.outputs[0].token_ids))
                    elapsed_ms = (time.monotonic_ns() - start_ns) // 1_000_000
                    await self.send(TranscriptionDelta(delta=delta, timestamp_ms=elapsed_ms))'''

    if old_block not in src:
        print("ERROR: connection.py pattern 2 not found", file=sys.stderr)
        sys.exit(1)

    src = src.replace(old_gen, new_gen)
    src = src.replace(old_block, new_block)

    with open(CONN_PATH, "w") as f:
        f.write(src)
    print(f"Patched {CONN_PATH}")


if __name__ == "__main__":
    patch_protocol()
    patch_connection()
    print("All realtime patches applied successfully")
