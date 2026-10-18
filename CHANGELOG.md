# Changelog

## 1.4.0
- **Real watch progress.** NyRun now asks VLC how far you are (through a local, VLC-only control port), so pressing play no longer marks a film as watched. A film or episode counts as watched once you get past 90% (a setting: 50-100%).
- **Resume where you stopped.** Half-watched films show `▶ 52%` on the poster and in the details; playing them again jumps back to your last position. *Continue* offers the film or episode you left half-way instead of skipping ahead.
- New *Watching* section in Settings: track progress, the "watched after" percentage, resume. Turning tracking off means nothing is marked automatically (press `w`).
- *Add a file*: removed the little file icon in front of file names in the picker.
- Albums are still marked as listened when you press play (Settings: *Mark albums as listened when I press play*).

## 1.3.2
- Home: *Continue* only shows when there is something to resume (a series with an episode left, or a film you started and did not mark finished); *Now playing* only shows once you have played music this session.
- README: removed the Development section.

## 1.3.1
- install.sh: install/update now remove the old copy first, which fixes "virtual environment already exists" on pipx versions that use uv.

## 1.3.0
- Requires Python 3.12+ (the poster library needs it).
- `install.sh`: a small menu to install, update or uninstall NyRun (sets up pipx if needed, checks for VLC, never touches your library).

## 1.2.0
- **Add a file**: guided flow (`a` on Home, tile, or `nyrun -add [FILE]`) - pick a file, choose the category, name it, optional poster image; NyRun creates the folder and puts both inside.
- New **Music** category: `Music/` folder (created by `-createfolder`), albums with covers in the library grid, import support (NyRun picks Music for audio files).
- **Now Playing** screen with a spinning pixel-art vinyl record that uses the album cover as its label, progress, track list, pause / next / previous / seek / volume / repeat / shuffle. Music plays in a window-less VLC that NyRun controls (can be switched off in Settings). The music keeps playing while you browse; a *Now playing* tile on the home screen returns to it.
- Album covers can come from a folder image or from the artwork embedded in the audio files; titles, artists and track lengths are read from the tags (new dependency: mutagen).
- The line under the logo is now exactly as wide as the logo, and a setting lets it match your theme (or Omarchy theme) instead of the rainbow.
- Home screen contents are now properly centred.
- Removed *Surprise me* (and the random pick in the library).
- Import description now says "move / copy from Downloads or any directory".

## 1.1.0
- Matches your **Omarchy** theme (reads `colors.toml`, with alacritty/kitty fallbacks) and follows it live when you switch themes.
- New theme picker: `nyrun -theme` (and `T` inside the app) with live preview, 16 built-in themes, terminal colours, and your own themes in `~/.config/nyrun/themes/`.
- New settings screen (`,` / `nyrun -vlc`) with VLC compatibility options: Wayland/X11 fix, hardware decoding, video/audio output, Flatpak launcher, language and resume options, extra flags, presets and a Test VLC button.
- If VLC exits with an error right after launch, NyRun now shows VLC's message.
- `nyrun -doctor` reports VLC version, theme resolution and the exact VLC command.

## 1.0.0
- First release: poster grid TUI, series/episode browser, import (cut/copy) screen,
  watched/favorites, search/sort/views, surprise-me, continue-watching, 4 themes.
- CLI: `-createfolder`, `-default`, `-import`, `-vlc`, `-theme`, `-list`, `-doctor`.
