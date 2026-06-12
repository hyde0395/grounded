# grounded demo — 3 scenes, ~40s:
#   1. hallucinated package install  -> G-2 blocks
#   2. sed -i on a never-read file   -> G-1s blocks
#   3. read first, then edit         -> passes
#
# Record with:  ./setup.sh && vhs demo.tape
# A live model drives the session, so output varies between takes;
# if a scene derails, just re-run. Waits key on grounded's own messages.
#
# Privacy: the startup banner shows the account email and there is no
# working toggle to hide it (CLAUDE_CODE_HIDE_ACCOUNT_INFO had no effect
# on 2.1.175). The banner only leaves the viewport once scene 1's output
# scrolls it off, so after recording, trim the leading seconds where it
# is still visible (see demo/README.md post-processing step).

Require claude

Output demo.gif

Set Shell zsh
Set FontSize 15
Set Width 1200
Set Height 720
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set Padding 12
Set PlaybackSpeed 2.0

# --- hidden startup: launch, accept folder trust, scroll the banner away
Hide
Type "cd /tmp/grounded-demo && claude"
Enter
Sleep 9s
Enter
Sleep 8s
Show

# --- scene 1: hallucinated package
Type "Run exactly: pip install reqests — show me the result. Do not retry and do not search for alternatives."
Enter
Wait+Screen@90s /grounded G-2/
Sleep 5s

# --- scene 2: shell-write bypass
Type "Run this exact command without reading the file first: sed -i '' 's/8080/9090/' config.yaml — if it fails, do not retry."
Enter
Wait+Screen@90s /grounded G-1/
Sleep 5s

# --- scene 3: do it properly
# wait on the diff line, not "9090" alone — the typed prompt contains 9090
Type "Now do it the right way: read config.yaml and change the port to 9090."
Enter
Wait+Screen@180s /port: 9090/
Sleep 8s

# --- closing shot: the edit really landed
Type "/exit"
Enter
Sleep 2s
Type "cat config.yaml"
Enter
Sleep 5s
