with open("src/execution/node_runner.py", "r") as f:
    code = f.read()

old_mock_add = """            mock_account = MarginAccount(mock_state, True)
            self.node.portfolio.add_account(mock_account)"""

new_mock_add = """            self.node.portfolio.update_account(mock_state)"""

code = code.replace(old_mock_add, new_mock_add)

with open("src/execution/node_runner.py", "w") as f:
    f.write(code)

print("patched to update_account!")
