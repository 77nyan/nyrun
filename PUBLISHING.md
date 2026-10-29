# Publishing NyRun - a step-by-step tutorial

Written for "I've never done this before". Every command can be copy-pasted.
Part 1 gets NyRun onto GitHub. Parts 2-4 are optional extras.

---

## 0. What's in this folder

```
nyrun/
├── src/nyrun/          the program itself (cli.py is the `nyrun` command)
├── tests/              automated tests
├── pyproject.toml      package info: name, version, dependencies, the `nyrun` command
├── README.md           the page people see on GitHub
├── LICENSE             MIT licence (change the name/year if you want)
├── CHANGELOG.md        what changed in each version
├── PUBLISHING.md       this file (you can delete it before publishing)
└── .github/workflows/  makes GitHub run the tests automatically on every push
```

## 1. Try it locally first (2 minutes)

```bash
cd nyrun
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                              # should end with "passed"
nyrun -doctor                       # checks VLC etc.
nyrun -createfolder ~/NyRun-test    # then drop a film folder with a poster in Movies/ and run: nyrun
```

## 2. Put it on GitHub

### 2a. One-time setup

Install git (`sudo pacman -S git` / `sudo apt install git` / https://git-scm.com) and tell it who you are:

```bash
git config --global user.name "Jakub Ziaja"
git config --global user.email "YOUR_GITHUB_NOREPLY_EMAIL"
```

> Your commits are public. On github.com go to **Settings > Emails**, tick **Keep my email addresses private**
> and use the `1234567+77nyan@users.noreply.github.com` address shown there, not your real one.

### 2b. Log in (the easy way: GitHub CLI)

GitHub no longer accepts your password on the command line. The GitHub CLI handles login for you:

```bash
# install: https://cli.github.com   (Arch: sudo pacman -S github-cli, Debian/Ubuntu: sudo apt install gh)
gh auth login
```

Pick **GitHub.com > HTTPS > Login with a web browser** and follow the prompts.

### 2c. Create the repo and push (one command)

```bash
cd nyrun
git init -b main
git add .
git commit -m "NyRun 1.2.0"
gh repo create 77nyan/nyrun --public --source=. --remote=origin --push \
  --description "A cozy poster-based TUI video and music selector that launches your films, shows and albums in VLC"
```

Done - open https://github.com/77nyan/nyrun.

### 2c (alternative, no `gh`)

1. On github.com click **+ > New repository**. Name: `nyrun`. **Public**. Do **not** tick "Add a README / .gitignore / licence" (you already have them). Create.
2. Then:

```bash
cd nyrun
git init -b main
git add .
git commit -m "NyRun 1.2.0"
git remote add origin https://github.com/77nyan/nyrun.git
git push -u origin main
```

When asked for a password, GitHub wants a **Personal Access Token** instead: github.com > Settings > Developer settings >
Personal access tokens > Tokens (classic) > Generate new token, tick **repo**, copy it, paste it as the password.
(Or set up an SSH key - GitHub's guide: "Connecting to GitHub with SSH".)

### 2d. Make the repo page look good

- On the repo page click the gear next to **About**: add the website/description and topics:
  `tui`, `vlc`, `terminal`, `textual`, `python`, `video`, `media-launcher`.
- Take a screenshot of NyRun running in your terminal, save it as `docs/screenshot.png`, and add this line under the title in `README.md`:
  `![NyRun](docs/screenshot.png)`
  then `git add . && git commit -m "Add screenshot" && git push`.
- The **Actions** tab should show the tests going green after your push.

### 2e. Make an official release

```bash
git tag v1.2.0
git push origin v1.2.0
```

Then on GitHub: **Releases > Draft a new release**, choose tag `v1.2.0`, paste the 1.2.0 notes from `CHANGELOG.md`, **Publish release**.

People can now install it from GitHub with the installer (the only supported way):

```bash
git clone https://github.com/77nyan/nyrun && cd nyrun
sudo ./install.sh
```

## 3. (Optional) Publish on PyPI

NyRun is installed with `sudo ./install.sh`; a PyPI upload is only needed if you also want `pip` users.

1. Check the name is free: open https://pypi.org/project/nyrun/ - if it exists, pick another name
   (change `name = "nyrun"` in `pyproject.toml`; the command can stay `nyrun`).
2. Create an account at https://pypi.org, turn on 2-factor authentication, then
   **Account settings > API tokens > Add API token** (scope: entire account for the first upload). Copy the token (starts with `pypi-`).
3. Build and upload:

```bash
pip install build twine
python -m build                 # creates dist/nyrun-1.2.0.whl and .tar.gz
twine upload dist/*             # username: __token__    password: your pypi- token
```

Want a dry run first? `twine upload --repository testpypi dist/*` uploads to https://test.pypi.org instead.

## 4. (Optional) The AUR (Arch Linux)

NyRun depends on `textual-image`, which is not in the official Arch repos at the moment, so an AUR package needs that dependency
available from the AUR as well. The straightforward order is: publish on PyPI (part 3), then write a `PKGBUILD` that builds from the
PyPI/GitHub tarball. Ask me when you get there and I'll write it with you.

## 5. Releasing a new version later

1. Change the version in **both** `pyproject.toml` and `src/nyrun/__init__.py`.
2. Add a section to `CHANGELOG.md`.
3. ```bash
   git add . && git commit -m "NyRun 1.2.0"
   git tag v1.2.0
   git push && git push origin v1.2.0
   ```
4. Make a GitHub release for the tag. For PyPI: `rm -rf dist && python -m build && twine upload dist/*`
   (PyPI never lets you re-upload the same version number, so always bump it).

## Checklist before you push

- [ ] `pytest` passes
- [ ] `nyrun -doctor` is happy on your machine
- [ ] LICENSE has the right name
- [ ] README links use your GitHub username (they already say `77nyan`)
- [ ] No personal files are in the folder (`git status` shows exactly what will be uploaded)
