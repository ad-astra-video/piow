#!/usr/bin/env python3
"""Patch vLLM realtime endpoint to ensure every frame pass emits transcription.delta.

Without this patch, vLLM only sends transcription.delta when delta text is
non-empty.  Silent frames (token_ids present but empty text) are dropped,
so the frontend receives no heartbeat signal during pauses.

This patch ensures an empty-string delta is sent for every frame pass so
the downstream pipeline can track frame timing.

Target: vllm v0.20.1
"""
import sys

CONN_PATH = "/usr/local/lib/python3.12/site-packages/vllm/entrypoints/openai/realtime/connection.py"


def patch_connection():
    with open(CONN_PATH, "r") as f:
        src = f.read()

    # Replace the delta / send block so that every frame pass emits a
    # TranscriptionDelta, even when delta text is empty.
    old_block = '''                    delta = output.outputs[0].text
                    full_text += delta

                    # append output to input
                    input_stream.put_nowait(list(output.outputs[0].token_ids))
                    await self.send(TranscriptionDelta(delta=delta))'''

    new_block = '''                    delta = output.outputs[0].text

                    # Always send a TranscriptionDelta for every frame pass so
                    # the downstream pipeline receives heartbeat signals, even
                    # when the model generates tokens with no text output.
                    if delta:
                        full_text += delta

                    # append output to input
                    input_stream.put_nowait(list(output.outputs[0].token_ids))
                    await self.send(TranscriptionDelta(delta=delta))'''

    if old_block not in src:
        print("ERROR: connection.py pattern not found", file=sys.stderr)
        sys.exit(1)

    src = src.replace(old_block, new_block)

    with open(CONN_PATH, "w") as f:
        f.write(src)
    print(f"Patched {CONN_PATH}")


if __name__ == "__main__":
    patch_connection()
    print("Realtime heartbeat patch applied successfully")
