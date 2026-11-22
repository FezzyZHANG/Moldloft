# Workbench design

The canvas shows ticket dependencies and note links. Card accents identify
the item, line patterns identify the relationship, and the inspector holds the
editable text. For the graph rules, see [Architecture](ARCHITECTURE.md#graph-reduction).

## Color and themes

| Role | CSS properties | Where it appears |
| --- | --- | --- |
| Ground and ink | `--ground`, `--panel`, `--panel2`, `--ink`, `--ink2`, `--rule`, `--rule2` | Workspace, panes, text and dividers. Cards use `--node-bg` and `--node-border` on `--canvas-bg`. |
| Muted | `--muted` | Closed ticket accents and `relates` edges. |
| Danger | `--danger` | Blocked accents, `challenges` edges, drifted or missing pins, save conflicts and deletion previews. |
| Attention | `--attn`, `--attn-soft`, `--attn-glow` | Selection, active work, assumptions, unsaved drafts and the ready-card shadow. |
| Information | `--info`, `--info-soft` | Ready ticket accents, note borders, question accents and `supports` edges. |
| Structure | `--struct` | Group headings, done tickets, decision accents and added cards. Group frames use `--group-bg` and `--group-border`. |

`theme.js` sets `data-theme` on the document root and saves the choice under
`moldloft.theme.v1` in local storage. An absent or invalid preference selects
`endfield`. Endfield and Monokai are dark. Loft follows the OS color scheme.
See the [theme registry](src/moldloft/static/theme.js) and [CSS definitions](src/moldloft/static/app.css).

| Theme ID and label | Ground | Muted | Danger | Attention | Information | Structure |
| --- | --- | --- | --- | --- | --- | --- |
| `endfield` · 终末地 · Endfield | `#0D1015` | `#667385` | `#FF4757` | `#F0E100` | `#62B8FF` | `#34D399` |
| `monokai` · Monokai | `#22231E` | `#75715E` | `#F92672` | `#E6DB74` | `#66D9EF` | `#A6E22E` |
| `loft` · 放样间 · Loft (light) | `#DDD9CC` | `#7A7D77` | `#B83524` | `#B87A0A` | `#1D5A8A` | `#1F6E50` |
| `loft` · 放样间 · Loft (dark) | `#0A1012` | `#71817C` | `#DC6A50` | `#E0AE46` | `#7CC2F5` | `#46C48A` |

## Cards and accents

Ticket and note faces are 114 SVG units tall and at most 244 wide. The title
wraps to two lines. A ticket's solid left bar is 4 units wide. Ready uses
`--info`, in-progress uses `--attn` with a 6 px glow, and unfinished blockers
override the fill with `--danger` on active tickets. Terminal tickets use
`--struct` for `done` and `--muted` for `closed`. Terminal cards have opacity
0.74, rising to 1 when selected.

A ready ticket is open with no unfinished direct blockers. Its surface has a
1.6-unit `--attn-soft` stroke and three consecutive `drop-shadow()` filters.

| Ready surface | Hard offset | Glow | Depth |
| --- | --- | --- | --- |
| At rest | `5px 5px 0 var(--attn-glow)` | `0 0 12px var(--attn-glow)` | `0 4px 8px rgba(0, 0, 0, .38)` |
| Hover | `6px 6px 0 var(--attn-glow)` | `0 0 18px var(--attn-glow)` | `0 6px 12px rgba(0, 0, 0, .42)` |

Selection gives a card a 2.5-unit attention stroke and a 9 px attention glow.
The ready-hover filter still takes precedence on a hovered ready card.

Notes have an information-colored border. `noteAccentDashes(height)` draws the
left accent as filled parallelograms with 45° and 135° corners. Each polygon
has vertices `(0,y+5) (5,y) (5,y+7) (0,y+12)`. The first starts at `y=2`,
subsequent starts are 12 units apart, and a dash is drawn while
`y + 12 <= height - 2`. Assumptions use `--attn`, questions `--info-soft`,
evidence and general notes `--info`, and decisions `--struct`.
See the [SVG drawing code](src/moldloft/static/canvas.js).

## Edges and navigation

| Canvas edge | Fallback token | Width and dash array (SVG units) | End marker (SVG path) |
| --- | --- | --- | --- |
| `requires` (blocker → blocked ticket) | `--edge-requires` | 2.5, solid | Filled triangle `M0 1 L10 5 L0 9 z` |
| `assumes` | `--attn-soft` | 2, `10 5` | Open chevron `M1 1 L9 5 L1 9` |
| `supports` | `--info` | 5, solid, with a 1.8-unit `--canvas-bg` line drawn over its center to form two rails | Same filled triangle as `requires` |
| `challenges` | `--danger` | 2.6, `9 3 2 3` | Terminal bar and stem `M8 1 L8 9 M0 5 L8 5` |
| `relates` | `--muted` | 1.6, `2 5`, round caps | None |

Each rendered edge receives a `userSpaceOnUse` SVG linear gradient from its
source node's accent tone (ticket state or note kind) to its target node's
accent tone. Filled arrowheads and open markers share the line's gradient through
SVG `context-stroke`. Browsers without context paint use the target accent for the
marker. Hover turns the line and marker to `--attn`. Removed edges keep `--danger`
on hover.
Markers use a 10 × 10 viewBox, a 7 × 7 marker size and reference point (9, 5).
They appear on terminal route sections. Open markers use a 1.85-unit stroke
with round caps and joins. A transparent 12-unit stroke gives
every edge a continuous click target. Clicking a dependency selects its
blocked ticket. Clicking a note link selects its source note. A bundled edge
uses the first underlying relation. Hovering a card highlights adjacent edges
at width 3.2 (5.8 for `supports`) and dims other edges to opacity 0.23.

Drag empty canvas to pan, scroll to zoom about the pointer, or double-click
empty canvas to fit. Revealing a card below 0.55× zoom centers it at 0.85×.
At higher zoom, reveal recenters only when the card center falls outside the
viewport's 24 px inset.

## Panes and sidebar

The navigator starts at 250 px (range 180–520) and the inspector at 360 px
(range 260–680). The desktop grid uses 5 px splitters and a 260 px center
minimum when either pane is open. Drag a splitter to resize. Pane widths and
collapse flags are saved under `moldloft.panes.v1`. At 730 px viewport width
or less the panes stack and the splitters and pane buttons are hidden.

- **Ticket row.** A non-wrapping `#ID` sits above its localized status badge
  in a leading column at least 44 px wide. The title occupies the space to its right.
- **Note row.** The title spans two grid rows on the left, clamped to two lines.
  The note ID is right-aligned on the top row and ellipsized beyond 17ch. The
  second row places state and kind side by side, in that order. The ID tooltip
  shows the full value.
- **List view.** The table puts ID and title in the first column, followed by
  status/state, priority/kind and group/location.

## Keyboard controls

| Context | Keys | Action |
| --- | --- | --- |
| Workbench | Ctrl/Cmd+K | Toggle the command palette. Opening requires that there is no other dialog open and IME composition is inactive. |
| Palette search | ↑ / ↓, Enter, Esc | Cycle through results, choose a result, or close the palette with one Esc. |
| Inspector draft | Ctrl/Cmd+S | Save after any active IME composition finishes. |
| Panes | `Alt+[` / `Alt+]` | Toggle the left / right pane. |
| Focused splitter | ← / →, Shift+← / → | Move the divider 16 px or 48 px with Shift. |
| Focused splitter | Home or double-click | Restore that pane's default width and expand it. |
| Focused canvas | Arrows, Shift+arrows | Pan 32 px or 96 px with Shift. |
| Focused canvas | `+` or `=`, `-`, `0` | Zoom in or out by a factor of 1.2, or fit the graph. |
| Focused card or collapse control | Enter or Space | Activate the card or toggle its children. |
| Connecting tickets | Esc | Cancel the connection when no dialog is open. |

## Markdown preview and saving

The inspector previews ticket descriptions, technical notes and note bodies.
`markdown.js` builds DOM nodes and assigns text directly. Its block syntax
covers paragraphs, headings of levels 1–6, blockquotes, unordered lists
(`-`, `*`, `+`), numbered lists (`1.`), task items (`[ ]`, `[x]` or `[X]` after a
bullet), horizontal rules (three or more hyphens or asterisks), and
triple-backtick fences with an optional language label. Task markers are
display-only. Inline syntax covers backtick code, asterisk emphasis and strong
text, double-tilde strikethrough, and navigable item references such as `#12`
and `n-window`. HTML and Markdown URL syntax display as text.

The inspector saves when focus leaves it, on an outside pointer press, window
blur, a hidden tab, Ctrl/Cmd+S or before a guarded action such as switching
graphs. A save waits for composition to end and the final input event to land.
Background refreshes preserve unsaved fields. A failed save offers retry. On a
version conflict, the inspector shows the server and draft values and lets the
user load the server version or reapply the local edits. If the item was
deleted, the draft stays available to copy or discard.
