"""Textual CSS for NyRun. Uses theme variables only, so every theme just works."""

CSS = """
Screen { background: $background; }

/* ---------- home ---------- */
#home-wrap { align: center middle; height: 1fr; }
#logo { width: 100%; height: auto; margin-bottom: 0; content-align: center top; }
#rainbow { width: 100%; height: 1; margin-bottom: 1; content-align: center top; }
#tagline { width: 100%; height: 1; color: $text-muted; margin-bottom: 1; content-align: center top; text-wrap: nowrap; text-overflow: ellipsis; }
.tile-row { width: 100%; height: auto; margin-bottom: 1; align-horizontal: center; }
.tile {
    width: 25; height: 7;
    border: round $primary 60%;
    background: $surface;
    content-align: center middle;
    text-align: center;
    margin: 0 1;
}
.tile.-action { width: 20; height: 6; margin: 0 1; }
.tile:hover { border: round $primary; }
.tile:focus { border: heavy $accent; background: $boost; }

/* ---------- library ---------- */
#lib-body { height: 1fr; }
#grid-wrap { width: 1fr; height: 1fr; padding: 0 1; }
#search { display: none; margin-bottom: 0; border: tall $primary 60%; }
#search.-open { display: block; }
#status-bar { height: 1; color: $text-muted; margin-bottom: 1; }
#grid { height: 1fr; width: 1fr; overflow: hidden; }
.card-row { height: auto; width: 100%; }
.card {
    height: auto;
    border: round $panel;
    background: $surface;
    margin: 0 1 0 0;
}
.card.-selected { border: heavy $accent; background: $boost; }
.card-title { width: 100%; text-style: bold; text-wrap: nowrap; text-overflow: ellipsis; }
.card-sub { width: 100%; color: $text-muted; text-wrap: nowrap; text-overflow: ellipsis; }
.card.-selected .card-sub { color: $accent; }
.empty-lib { width: 100%; height: 1fr; content-align: center middle; text-align: center; color: $text-muted; }

#detail {
    width: 40; height: 1fr;
    border-left: tall $primary 40%;
    padding: 1 2;
    background: $surface 40%;
}
#detail-poster-box { width: 100%; height: auto; align-horizontal: center; margin-bottom: 1; display: none; }
#detail-title { text-style: bold; color: $accent; width: 100%; }
#detail-info { width: 100%; height: auto; }

/* ---------- episodes ---------- */
#ep-body { height: 1fr; padding: 1 2; }
#ep-side { width: 40; height: 1fr; padding-right: 2; }
#ep-list { width: 1fr; height: 1fr; border: round $primary 50%; background: $surface; }
#ep-list:focus { border: round $accent; }
#ep-title { text-style: bold; color: $accent; width: 100%; margin-top: 1; }
#ep-info { color: $text-muted; width: 100%; height: auto; }

/* ---------- import ---------- */
#imp-body { height: 1fr; padding: 1 2; }
#imp-left { width: 1fr; height: 1fr; padding-right: 2; }
#imp-path { height: 1; color: $text-muted; margin-bottom: 1; }
#imp-tree { height: 1fr; border: round $primary 50%; background: $surface; }
#imp-tree:focus { border: round $accent; }
#imp-form { width: 50; height: 1fr; overflow-y: auto; }
#imp-selected { height: auto; margin-bottom: 1; color: $accent; text-style: bold; }
.imp-label { color: $text-muted; margin-top: 1; }
#imp-go { margin-top: 1; width: 100%; }
#imp-hint { color: $text-muted; height: auto; margin-bottom: 1; }

/* ---------- add a file ---------- */
#add-body { height: 1fr; padding: 1 2; }
#add-title { text-style: bold; color: $accent; margin-bottom: 1; width: 100%; }
#add-info { color: $text-muted; height: auto; margin-bottom: 1; }
#add-tree, #add-cat { height: 1fr; border: round $primary 50%; background: $surface; }
#add-cat { height: 8; }
#add-name, #add-image { width: 80; }
#add-mode { width: 60; }

/* ---------- modals ---------- */
ModalScreen { align: center middle; background: $background 70%; }
#modal-box {
    width: 62; height: auto; max-height: 90%;
    border: heavy $accent; background: $surface; padding: 1 2;
}
#modal-poster { width: 100%; height: auto; align-horizontal: center; margin-bottom: 1; }
#modal-title { width: 100%; text-style: bold; text-align: center; color: $accent; }
#modal-sub { width: 100%; text-align: center; color: $text-muted; margin-bottom: 1; }
#modal-buttons { width: 100%; height: auto; align-horizontal: center; }
#modal-buttons Button { margin: 0 1; }
#help-text { width: 100%; height: auto; }

/* ---------- theme picker ---------- */
#tp-body { height: 1fr; padding: 1 2; }
#tp-left { width: 1fr; height: 1fr; padding-right: 2; }
#tp-title { color: $text-muted; margin-bottom: 1; width: 100%; }
#tp-list { height: 1fr; border: round $accent; background: $surface; }
#tp-right { width: 44; height: 1fr; }
.tp-h { text-style: bold; color: $accent; margin-bottom: 1; }
#tp-card { width: 22; margin-bottom: 1; padding: 0 1; height: 6; content-align: center middle; text-align: center; }
#tp-tile { width: 30; height: 6; margin: 0 0 1 0; }
#tp-note { width: 100%; height: auto; margin-bottom: 1; }
#tp-help { width: 100%; height: auto; color: $text-muted; }

/* ---------- settings ---------- */
#st-body { padding: 1 2; height: 1fr; }
.st-h { text-style: bold; color: $accent; margin-top: 1; width: 100%; border-bottom: heavy $panel; }
.st-row { height: auto; width: 100%; margin-top: 0; }
.st-label { width: 34; padding-top: 1; }
.st-row Select, .st-row Input { width: 1fr; max-width: 80; }
.st-hint { color: $text-muted; padding-left: 34; width: 100%; height: auto; }
#st-theme-now { padding-left: 34; }
#st-preview { width: 100%; height: auto; padding: 1 2; background: $surface; border: round $primary 50%; margin-top: 1; color: $accent; }
#st-warn { width: 100%; height: auto; color: $warning; margin-top: 1; }
#st-buttons { height: auto; margin-top: 1; margin-bottom: 2; }
#st-buttons Button { margin-right: 2; }

/* ---------- now playing ---------- */
#np-body { height: 1fr; padding: 1 2; }
#np-left { width: 52; height: 1fr; padding-right: 2; }
#np-disc-box { width: 100%; height: auto; align-horizontal: center; margin-bottom: 1; }
#np-title { text-style: bold; color: $accent; width: 100%; text-align: center; text-wrap: nowrap; text-overflow: ellipsis; }
#np-sub { width: 100%; height: auto; text-align: center; color: $text-muted; }
#np-time { width: 100%; height: 1; margin-top: 1; }
#np-status { width: 100%; height: auto; margin-top: 1; color: $text-muted; }
#np-right { width: 1fr; height: 1fr; }
#np-tracks-h { color: $text-muted; margin-bottom: 1; width: 100%; }
#np-tracks { height: 1fr; border: round $primary 50%; background: $surface; }
#np-tracks:focus { border: round $accent; }
"""
