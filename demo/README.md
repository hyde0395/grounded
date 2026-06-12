# Demo recording

Reproducible 30-second GIF for the README, recorded with
[vhs](https://github.com/charmbracelet/vhs).

```bash
brew install vhs        # one-time
./setup.sh              # fresh sandbox in /tmp/grounded-demo
vhs demo.tape           # writes demo.gif
```

Post-processing: the Claude Code startup banner shows the account email and
no toggle hides it (tested `CLAUDE_CODE_HIDE_ACCOUNT_INFO`, `/clear`,
`Ctrl+L` on 2.1.175), so trim the opening seconds where the banner is still
in the viewport. Find the first clean second by extracting frames
(`ffmpeg -i demo.gif -vf fps=1 frames/f%03d.png`), then:

```bash
ffmpeg -ss <T> -i demo.gif \
  -vf "split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer" \
  -loop 0 demo_final.gif
```

Three scenes:

1. `pip install reqests` → **G-2 blocks** (package doesn't exist on PyPI)
2. `sed -i` on a file never read → **G-1s blocks**
3. read the file, then edit → passes; closing shot proves the change landed

Notes:

- The session inside the recording is a *real* Claude Code session with
  grounded's hooks wired via the sandbox `.claude/settings.json` — nothing
  is mocked. That also means takes vary; if the model goes off-script in a
  scene, re-run `./setup.sh && vhs demo.tape`.
- Scene 1 needs network (a live PyPI lookup).
- The tape waits on grounded's own messages (`[grounded G-2]`,
  `[grounded G-1]`), so a successful render means the blocks really fired.
