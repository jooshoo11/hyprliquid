import re

with open("src/execution/node_app.py", "r") as f:
    code = f.read()

old_update = """
            if coin in current_rows:
                table.update_row(str(current_rows[coin]), *row_data)
            else:
                table.add_row(*row_data, key=str(idx))
"""

new_update = """
            if str(idx) in current_rows:
                for col_idx, val in enumerate(row_data):
                    table.update_cell(str(idx), table.columns[list(table.columns.keys())[col_idx]].key, val)
            else:
                table.add_row(*row_data, key=str(idx))
"""

# Wait, `current_rows` was defined as:
# `current_rows = {table.get_row_at(i)[0].plain: i for i in range(table.row_count)} if table.row_count > 0 else {}`
# So `str(idx)` is NOT in current_rows, the coin name is!
# But the row key we use when adding is `key=str(idx)`.
# Textual `DataTable` uses string RowKey objects. We should check if row key exists.

new_update = """
            try:
                for col_idx, val in enumerate(row_data):
                    table.update_cell(str(idx), table.columns[list(table.columns.keys())[col_idx]].key, val)
            except Exception:
                table.add_row(*row_data, key=str(idx))
"""

code = code.replace(old_update, new_update)

with open("src/execution/node_app.py", "w") as f:
    f.write(code)

print("patched!")
