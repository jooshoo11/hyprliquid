import os
import re

cont_path = "/home/jooshoo/Desktop/hyprliquid/src/strategies/continuation.py"
with open(cont_path, "r") as f:
    cont_code = f.read()

old_swing = """        last_high = swing_data['HighLevel'].last_valid_index()
        last_low = swing_data['LowLevel'].last_valid_index()
        
        if last_high is not None:
            state.recent_swing_high = swing_data.loc[last_high, 'HighLevel']
            state.recent_swing_wick_high = df.loc[last_high, 'high']
        
        if last_low is not None:
            state.recent_swing_low = swing_data.loc[last_low, 'LowLevel']
            state.recent_swing_wick_low = df.loc[last_low, 'low']"""

new_swing = """        highs = swing_data[swing_data['HighLow'] == 1]
        lows = swing_data[swing_data['HighLow'] == -1]
        
        last_high = highs.last_valid_index() if not highs.empty else None
        last_low = lows.last_valid_index() if not lows.empty else None
        
        if last_high is not None:
            state.recent_swing_high = highs.loc[last_high, 'Level']
            state.recent_swing_wick_high = df.loc[last_high, 'high']
        
        if last_low is not None:
            state.recent_swing_low = lows.loc[last_low, 'Level']
            state.recent_swing_wick_low = df.loc[last_low, 'low']"""

cont_code = cont_code.replace(old_swing, new_swing)

# Same for fvg:
# Let's see what fvg returns
