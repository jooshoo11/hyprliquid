-- ==============================================================================
-- Hyperliquid Autonomous AI Cockpit - WezTerm Configuration (wezterm.lua)
-- Institutional Dark Terminal Cockpit for Autonomous Multi-Strategy Trading
-- ==============================================================================

local wezterm = require 'wezterm'
local config = wezterm.config_builder()

-- 1. Color Palette: Hyperliquid Dark Cyberpunk Cockpit
config.colors = {
  background = '#080c14',
  foreground = '#f1f5f9',
  cursor_bg = '#38bdf8',
  cursor_fg = '#080c14',
  cursor_border = '#38bdf8',
  selection_bg = '#1e293b',
  selection_fg = '#f8fafc',
  scrollbar_thumb = '#1e293b',
  split = '#1e293b',

  ansi = {
    '#0b1120', -- black
    '#f43f5e', -- red (losses / short / alert)
    '#10b981', -- green (profit / long / healthy)
    '#f59e0b', -- yellow (paper / warning / guard)
    '#38bdf8', -- blue (hyperliquid cyan)
    '#a855f7', -- magenta (sentinel ai thoughts)
    '#06b6d4', -- cyan (macro info)
    '#cbd5e1', -- white
  },
  brights = {
    '#334155', -- bright black
    '#fb7185', -- bright red
    '#34d399', -- bright green
    '#fbbf24', -- bright yellow
    '#60a5fa', -- bright blue
    '#c084fc', -- bright magenta
    '#22d3ee', -- bright cyan
    '#ffffff', -- bright white
  },

  tab_bar = {
    background = '#06090f',
    active_tab = {
      bg_color = '#0f172a',
      fg_color = '#38bdf8',
      intensity = 'Bold',
      underline = 'None',
    },
    inactive_tab = {
      bg_color = '#080c14',
      fg_color = '#64748b',
    },
    inactive_tab_hover = {
      bg_color = '#1e293b',
      fg_color = '#e2e8f0',
    },
    new_tab = {
      bg_color = '#080c14',
      fg_color = '#64748b',
    },
  },
}

-- 2. Typography & Font Fallbacks
config.font = wezterm.font_with_fallback({
  'JetBrains Mono',
  'Symbols Nerd Font Mono',
  'Noto Color Emoji',
})
config.font_size = 11.0
config.line_height = 1.05

-- 3. Window Sizing & Rendering
config.initial_cols = 160
config.initial_rows = 44
config.window_background_opacity = 0.98
config.window_padding = {
  left = 8,
  right = 8,
  top = 6,
  bottom = 6,
}
config.window_decorations = "RESIZE"
config.enable_tab_bar = true
config.use_fancy_tab_bar = false
config.tab_bar_at_bottom = true
config.hide_tab_bar_if_only_one_tab = false

-- 4. Dynamic Telemetry Status Bar
wezterm.on('update-right-status', function(window, pane)
  local date = wezterm.strftime('%H:%M:%S UTC')

  window:set_right_status(wezterm.format({
    { Foreground = { Color = '#10b981' } },
    { Text = '● SENTINEL ONLINE  ' },
    { Foreground = { Color = '#f59e0b' } },
    { Text = '🛡️ GUARD: ACTIVE  ' },
    { Foreground = { Color = '#38bdf8' } },
    { Text = '⚡ ' .. date .. ' ' },
  }))
end)

-- 5. Default Launch Program
local project_dir = wezterm.config_dir
config.default_prog = {
  project_dir .. '/.venv/bin/python',
  '-m',
  'src.tui.app'
}

-- 6. Keyboard Shortcuts & Multiplexer Controls
local act = wezterm.action
config.keys = {
  { key = 'F11', action = act.ToggleFullScreen },
  { key = 'z', mods = 'ALT', action = act.TogglePaneZoomState },
  { key = 'v', mods = 'ALT', action = act.SplitHorizontal { domain = 'CurrentPaneDomain' } },
  { key = 's', mods = 'ALT', action = act.SplitVertical { domain = 'CurrentPaneDomain' } },
  { key = 'h', mods = 'ALT', action = act.ActivatePaneDirection 'Left' },
  { key = 'l', mods = 'ALT', action = act.ActivatePaneDirection 'Right' },
  { key = 'k', mods = 'ALT', action = act.ActivatePaneDirection 'Up' },
  { key = 'j', mods = 'ALT', action = act.ActivatePaneDirection 'Down' },
  { key = 'w', mods = 'ALT', action = act.CloseCurrentPane { confirm = true } },
}

return config
