with open("src/execution/node_runner.py", "r") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "self.is_ephemeral," in line:
        if "self.paper" not in lines[i+1]:
            lines.insert(i+1, line.replace("self.is_ephemeral", "self.paper"))

with open("src/execution/node_runner.py", "w") as f:
    f.writelines(lines)
