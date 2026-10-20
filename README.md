<h1 align="center">NyRun</h1>
<p align="center"><b>A cozy poster-based video and music selector for your terminal. Pick a film or an album, press Enter, VLC does the rest.</b></p>

```
 _   _       ____
| \ | |_   _|  _ \ _   _ _ __
|  \| | | | | |_) | | | | '_ \
| |\  | |_| |  _ <| |_| | | | |
|_| \_|\__, |_| \_\\__,_|_| |_|
       |___/
```

NyRun turns a plain folder of movies, shows and music into a browsable library right in
your terminal: a grid of posters and album covers, a details panel, watched marks, search, and a
one-key launch into **VLC**. No database, no accounts, no scraping the internet -
your files stay exactly where they are, and your posters are just image files.

## Features

- **Poster grid** with a details panel (size, duration, subtitles, play count, progress)
- **Movies / Series / Shows / Music** categories, with a season-aware **episode browser**
- **Music player** with a **spinning vinyl record** drawn in pixels, with the album cover as its label (see [Music](#music))
- **Add a file** (`a`, or `nyrun -add`): a guided 5-step flow. Pick a video, choose Movies / Series / Shows / Music, type the name, optionally give it a poster image, move or copy. NyRun creates the folder with that name and puts the file and the image inside
- **Watch from a URL** (`u`, or `nyrun -url LINK`): paste a video link, a stream (`.m3u8`, `.mpd`, `rtsp://`...) or a web page and it plays in VLC. NyRun has its own URL engine and uses yt-dlp for the sites that need it (see [Watching from a URL](#watching-from-a-url))
- **Import screen**: **move / copy from Downloads or any directory**. Highlight a film, a show, a song or a folder and **move (cut) or copy** it into your library. NyRun picks Music for audio automatically. A lone file gets its own folder and its subtitles and poster come along
- **Continue watching** (only shown when there is something to resume): jumps to the film or episode you left half-way (or your next unwatched episode), and the rest of the season is queued in VLC
- **Watched** and **favorite** marks, **search**, **sort** (name / added / last played / year / size) and **views** (all / unwatched / watched / favorites)
- **Matches your Omarchy theme** and follows it live: switch themes in Omarchy and NyRun recolours itself within a couple of seconds
- **Theme picker** (`nyrun -theme`) with live preview, 16 built-in themes, your own themes, or your terminal's own colours
- **VLC compatibility settings** you can change from inside NyRun (Wayland/X11 fix, hardware decoding, video/audio output, Flatpak, subtitle/audio language, resume, extra flags) with one-key presets and a *Test VLC* button
- Open-in-file-manager, VLC fullscreen toggle
- Real posters in terminals that support the Kitty graphics protocol or Sixel, with a colored half-block fallback everywhere else
- Auto-detects VLC (native, Flatpak, macOS app, Windows install). Posters are cached, so even 4000px scans stay snappy
- Your files are never deleted. NyRun only moves/copies when *you* press Import

## Install

You need Python 3.12+ and [VLC](https://www.videolan.org/vlc/).

```bash
pipx install git+https://github.com/77nyan/nyrun    # straight from GitHub
pipx install nyrun                                   # once it is on PyPI
```

**Installer script** (install / update / uninstall menu):

```bash
git clone https://github.com/77nyan/nyrun && cd nyrun
./install.sh
```

Run `./install.sh` again any time and pick *Update* to get the newest version, or *Uninstall* to remove it (your library is never touched).

From a local copy:

```bash
git clone https://github.com/77nyan/nyrun
cd nyrun
pipx install .
```

(No pipx? `pip install --user .` works too. On Debian/Ubuntu/Arch, `pipx` is the way to avoid the "externally managed environment" error.)

## Quick start

```bash
nyrun -createfolder          # creates ~/NyRun with Movies/ Series/ Shows/ Music/ and sets it as default
nyrun                        # open the library
```

Prefer another place (a big external drive, say)?

```bash
nyrun -createfolder /mnt/media/NyRun      # create it there (becomes default if you have none yet)
nyrun -default /mnt/media/NyRun           # or point NyRun at a folder you already have
```

### How to lay out your files

```
NyRun/
├── Movies/
│   ├── Inception (2010)/
│   │   ├── inception.mp4        <- any video: mp4 mkv avi mov webm ...
│   │   └── poster.jpg           <- any jpg / jpeg / png (this is what the TUI shows)
│   └── Spirited Away (2001)/ ...
├── Series/
│   └── Cowboy Bebop/
│       ├── cover.png
│       ├── Season 1/ep01.mkv, ep02.mkv ...
│       └── Season 2/ ...
└── Shows/
    └── Planet Earth/ ...
```

- One folder per title. The folder name is the title; `(2010)` at the end becomes the year.
- Poster = any `.jpg/.jpeg/.png` in the folder (names like `poster`, `cover`, `folder` win). No poster? You get a placeholder.
- A loose video in `Movies/` also works, with an image of the same name next to it.
- Folders that only contain a "sample" video are ignored. Hidden files are ignored.

### Bringing files in

Press **`i`** in NyRun (or choose *Import* on the home screen), browse to the file or folder,
choose the category and **Move (cut)** or **Copy**, then **Ctrl+G**. Or skip the TUI:

```bash
nyrun -import ~/Downloads/Inception.2010.1080p.mkv -to Movies -title "Inception (2010)"
nyrun -import ~/Downloads/Cool.Show -to Series -copy
```

Release-style names such as `Inception.2010.1080p.BluRay.x264` are tidied into `Inception (2010)` for you.

Already have a NyRun folder from an older version? Run `nyrun -default /path/to/it` once and the new `Music` folder is added.

## Keys

| Where | Key | Does |
|---|---|---|
| Home | arrows / Tab, Enter | move / open |
| Home | `1` `2` `3` `4` | Movies / Series / Shows / Music |
| Home | `a` | add a file (pick, choose where, name it, optional poster) |
| Home | `u` | watch from a URL |
| URL screen | Enter / `Ctrl+D` / `Ctrl+X` / `Ctrl+O` | play in VLC / save a copy / stop the download / open the downloads folder |
| URL screen | Down, Delete | go to the Recent list / forget a link |
| Home | `c` / `n` / `i` / `s` | continue watching / now playing / import / settings |
| Library | arrows, PgUp/PgDn, Home/End | move over posters / flip pages |
| Library | Enter | play a movie, open a series |
| Library | `p` | quick play (series: next unwatched episode) |
| Library | `w` / `f` | toggle watched / favorite |
| Library | `/` | search (Enter keeps the filter, Esc clears it) |
| Library | `s` / `v` | cycle sort / cycle view |
| Library | `o` / `i` | open folder / import |
| Music (Now playing) | Space / `n` / `p` | pause / next / previous |
| Music (Now playing) | Left, Right / `+` `-` | seek 10 s / volume |
| Music (Now playing) | `l` / `s` / `x` / Esc | repeat / shuffle / stop / back (music keeps playing) |
| Episodes | Enter | play from here, queue the rest |
| Episodes | `p` / `w` / `n` | play only this / toggle watched / jump to next unwatched |
| Everywhere | `t` | cycle through themes |
| Everywhere | `T` | theme picker (live preview, Enter keeps it, Esc cancels) |
| Everywhere | `,` (Home: also `s`) | settings: theme and VLC compatibility |
| Everywhere | `z` / `?` / `q` | VLC fullscreen / help / quit |

Mouse works too: click a poster to select it, click again to open.

## Command line

```
nyrun                          open the library
nyrun -createfolder [PATH]     create the NyRun folder (default ~/NyRun)
nyrun -default PATH            choose which folder nyrun opens
nyrun -add [FILE]              guided: pick a file, choose where it goes, name it, optional poster
nyrun -import PATH             move / copy a file or folder in from Downloads or any directory
                               (-to Series|Shows|Music, -copy, -title "Name")
nyrun -url [LINK]              watch a link in VLC (opens the URL screen, plays at once if you give a link)
nyrun -download LINK           save a link's video into "URL Downloads" (turn it on in Settings first)
nyrun -vlc                     VLC compatibility settings screen
nyrun -vlc PATH                tell nyrun where VLC is (only if auto-detect fails)
nyrun -theme                   theme picker with live preview
nyrun -theme NAME              set a theme directly (auto, omarchy, terminal, sakura, nord, ...)
nyrun -theme list              list every available theme
nyrun -theme FILE.toml         install your own theme file
nyrun -list                    print your library without the TUI
nyrun -doctor                  check VLC, ffmpeg, yt-dlp and poster rendering
nyrun -version
```

`--double-dash` spellings work as well (`nyrun --default PATH`).

## Watching from a URL

Press `u` on the home screen (or run `nyrun -url`), paste a link and press Enter. NyRun works out what the link is and hands VLC the real video address:

1. **The link itself**: `.mp4`, `.mkv`, `.webm`, audio files, HLS (`.m3u8`), DASH (`.mpd`) and stream addresses (`rtsp://`, `rtmp://`, `udp://`...).
2. **What the server says**: a link without an extension is checked (content type and first bytes).
3. **A scan of the web page**: `og:video`, `<video>` / `<source>`, JSON-LD, stream links in the page's scripts, and embedded player frames. Candidates are checked and the best working one wins.
4. **yt-dlp** for the sites that hide their video (YouTube, Vimeo, Twitch, Reddit and about a thousand more). By default it is only used when steps 1-3 are not enough, or for sites it knows well. You can set it to *never* or *always* in Settings.
5. **VLC's own scripts** as a last resort.

**Saving a copy.** It is off until you enable *Allow downloading from a URL* in Settings (`,`) > *Web video*. Then press `Ctrl+D` on the URL screen (or run `nyrun -download LINK`). The video lands in `URL Downloads/<Title>/` inside your NyRun folder, with a poster when the page has one, so you can add the folder to a category later. Interrupted downloads continue where they stopped.

**ffmpeg** is not part of VLC. NyRun detects it and uses it for HLS / DASH / live downloads and for joining separate video and audio tracks. Without it, plain files and ordinary HLS still download with the built-in downloader. Install it with `sudo pacman -S ffmpeg` (Arch), `sudo apt install ffmpeg`, `brew install ffmpeg`. Settings shows what is installed. For YouTube, a JavaScript runtime (`deno` or `node`) helps yt-dlp.

**Limits.** No program can open every URL that exists. Pages that need a login, services with copy protection (DRM: Netflix, Disney+, Spotify...), videos assembled by the site in a way nobody can read, and links that have expired will not work. NyRun then tells you why. It never tries to get around DRM or paywalls.

## Music

Put albums in `Music/`, one folder per album (or artist) - the same idea as films:

```
NyRun/Music/Moon Songs (2020)/01 - Intro.mp3
NyRun/Music/Moon Songs (2020)/02 - Waltz.mp3
NyRun/Music/Moon Songs (2020)/cover.jpg        <- the album cover (jpg, jpeg or png)
```

- **Covers:** `cover.jpg` / `folder.png` / any image in the album folder. If there is none, NyRun uses the artwork embedded in the mp3 / flac / m4a / ogg files.
- **Formats:** mp3, flac, ogg, opus, m4a, aac, wav, wma, aiff, ape - anything VLC plays.
- **Library:** albums appear on the home screen as the fourth tile (`4`) and in the grid with their covers, like films.
- **Now Playing:** Enter on an album starts it and opens a screen with a **spinning vinyl record** (pixel art, drawn with half-block characters so it works in every terminal). The cover is the label of the record; the record stops when you pause. Below it: title, artist and album from the tags, a progress bar, and the track list. Esc leaves the screen and the music keeps playing - the home screen's *Now playing* tile (`n`) takes you back.
- **Controls:** pause, next/previous, seek, volume, repeat (off / album / track) and shuffle. They work because NyRun plays music in a window-less VLC that it steers; if you would rather have VLC's own window, turn off *Control music from NyRun* in Settings.
- Starting a film pauses the music; quitting NyRun stops it.

## Themes

By default NyRun uses `auto`: if [Omarchy](https://omarchy.org) is installed it uses your current Omarchy theme (background, text, accent and the semantic colours are taken from its `colors.toml`, with light themes handled and contrast enforced so text stays readable). Switch themes in Omarchy and NyRun follows while it is open.

No Omarchy? Run `nyrun -theme` and pick one. Choices:

| Choice | Meaning |
|---|---|
| `auto` | Omarchy's theme if found, otherwise `sakura` |
| `omarchy` | always follow Omarchy |
| `terminal` | use your terminal's own colour scheme |
| `sakura`, `midnight`, `matcha`, `mono`, `catppuccin`, `catppuccin-latte`, `tokyo-night`, `gruvbox`, `nord`, `rose-pine`, `dracula`, `kanagawa`, `everforest`, `one-dark`, `solarized-dark`, `flexoki-light` | built-in themes |

**Your own theme:** save a file such as `~/.config/nyrun/themes/mytheme.toml` (or run `nyrun -theme /path/to/mytheme.toml` to install it). It uses the same format as Omarchy's `colors.toml`:

```toml
background = "#1a1b26"
foreground = "#c0caf5"
accent     = "#7aa2f7"
# optional: color1..color15 (or red/green/yellow/blue...), mode = "light"
```

Then `nyrun -theme mytheme`.

**The line under the logo:** by default it is an animated rainbow. In Settings (`,`) set *Line under the NyRun logo* to *Match my theme* and it flows through your theme's colours instead - your Omarchy theme if that is what you use. It is always exactly as wide as the logo. If your Omarchy lives somewhere unusual, set `NYRUN_OMARCHY_DIR` to its `current` folder.

## VLC settings

Press `,` inside NyRun (or run `nyrun -vlc`). Everything is saved and applied to the next thing you play, and the screen shows the exact command NyRun will run.

- **Presets:** *Default*, *Wayland fix*, *Safe mode* (software decoding, for black screens or crashes), *Quiet* (no first-run privacy prompt, no recent-files list)
- **Compatibility:** VLC path, launcher (auto / native / Flatpak), window system (`QT_QPA_PLATFORM` x11 or Wayland), hardware decoding, video output, audio output
- **Playback:** fullscreen, close VLC when finished, on-screen title, loop, start paused, always on top, one-instance mode, resume position, speed, subtitle/audio language, subtitle auto-detect
- **Extra flags:** any additional VLC options, for example `--deinterlace=1`
- **Test VLC** checks that VLC starts with your settings. If VLC quits within a few seconds of playing, NyRun shows VLC's error message instead of silently doing nothing.

Using the Flatpak VLC? It cannot see folders outside your home until you allow it, for example `flatpak override --user --filesystem=/mnt/media org.videolan.VLC`. The settings screen reminds you.

## Good to know

- **Where settings live:** `~/.config/nyrun/` (`config.json`, `state.json`) on Linux/macOS, `%APPDATA%\nyrun\` on Windows. Watched/favorite marks are stored relative to your NyRun folder, so you can move the whole library to another disk without losing them.
- **Durations** show up if [`ffprobe`](https://ffmpeg.org/) (part of ffmpeg) is installed. Optional.
- **Watch progress:** while a film plays, NyRun asks VLC how far you are (a control port on `127.0.0.1` that only exists while that VLC runs). A film or episode is marked watched once you pass 90% (change it in Settings > Watching), a half-watched one shows `▶ 52%`, shows up in *Continue*, and resumes where you stopped. Quitting NyRun never stops the movie, you just lose tracking until you start it again from NyRun. If your VLC has no `rc` interface, nothing is marked automatically; press `w`, or turn *Track how much I watched* off.
- Albums are marked as listened when you press play (Settings: *Mark albums as listened when I press play*). Toggle any item with `w`.
- A movie folder with several videos plays the biggest one (so trailers/extras are skipped). Put multi-part films in a single file or use Series.
- Environment variable `NYRUN_VLC` overrides the VLC path.
- VLC's own error output from the last launch is kept in `~/.cache/nyrun/vlc-last.log`.

## Troubleshooting

| Problem | Fix |
|---|---|
| "VLC was not found" | Install VLC, or run `nyrun -vlc /path/to/vlc` |
| VLC opens a black window, crashes or does nothing | Press `,` and try the *Safe mode* or *Wayland fix* preset, then *Test VLC* |
| NyRun's colours do not match Omarchy | Run `nyrun -doctor` to see which theme it found; set `NYRUN_OMARCHY_DIR` if Omarchy is in a custom place |
| Posters look blocky | Your terminal has no graphics support; try Kitty, WezTerm, foot, Ghostty or Windows Terminal. NyRun still works, just in half-blocks |
| Library looks empty | Run `nyrun -list`. Each title needs its own folder under `Movies/`, `Series/`, `Shows/` or `Music/` |
| Music starts but the buttons do nothing, or VLC exits at once | Open Settings (`,`) and turn off *Control music from NyRun* - VLC then opens its normal window. `~/.cache/nyrun/vlc-last.log` has VLC's message |
| Drive unplugged | NyRun tells you the folder is missing. Reconnect it, or `nyrun -default NEWPATH` |

`nyrun -doctor` checks most of this for you.

## License

MIT - see [LICENSE](LICENSE).
