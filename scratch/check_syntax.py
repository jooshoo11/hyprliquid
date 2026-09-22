with open("/tmp/cockpit_test.js") as f:
    text = f.read()

import re

# Check template literals
ticks = [m.start() for m in re.finditer(r"(?<!\\)`", text)]
print(f"Total backticks: {len(ticks)} (should be even: {len(ticks)%2==0})")

# Check braces
stack = []
lines = text.splitlines()
for l_idx, line in enumerate(lines, 1):
    for c_idx, char in enumerate(line):
        if char in "({[":
            stack.append((char, l_idx, c_idx, line.strip()))
        elif char in ")}]":
            if not stack:
                print(f"Extra '{char}' at line {l_idx}:{c_idx}")
                continue
            last, l_num, _, l_txt = stack[-1]
            if (last == "(" and char == ")") or (last == "{" and char == "}") or (last == "[" and char == "]"):
                stack.pop()
            else:
                print(f"Mismatch at {l_idx}:{c_idx}: expected close for '{last}' from line {l_num} ({l_txt}), got '{char}'")

print(f"Unclosed items remaining: {len(stack)}")
for item in stack[-5:]:
    print(f"  Unclosed '{item[0]}' from line {item[1]}: {item[3]}")
