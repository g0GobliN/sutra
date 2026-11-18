"""Print a large, realistic Python traceback.

A real debugging session usually carries a deep stack. This keeps the corpus
reproducible without depending on the local Python version's traceback format.
"""
import sys

frames = []
for i in range(500):
    frames.append(f'  File "/srv/app/src/module_{i}.py", line {i * 7}, in handler_{i}')
    frames.append(f"    return dispatch(payload, retries={i})")
sys.stderr.write("Traceback (most recent call last):\n")
sys.stderr.write("\n".join(frames) + "\n")
sys.stderr.write("ValueError: bad input in parser at offset 42\n")
