# grounded demo — 3 scenes, ~30s after editing:
#   1. hallucinated package install  -> G-2 blocks
#   2. sed -i on a never-read file   -> G-1s blocks
#   3. read first, then edit         -> passes
#
# Record with:  ./setup.sh && vhs demo.tape
# A live model drives the session, so output varies between takes;
# if a scene derails, just re-run. Waits key on grounded's own messages.

Require claude

Output demo.gif

Set Shell zsh
Set FontSize 15
Set Width 1200
Set Height 720
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms
Set Padding 12

Hide
Type "cd /tmp/grounded-demo && clear"
Enter
Show

# --- open Claude Code with grounded hooks active
Type "claude"
Enter
# fresh sandbox → folder-trust dialog; Enter accepts "Yes, I trust"
Sleep 8s
Enter
Sleep 8s

# --- scene 1: hallucinated package
Type "Run exactly: pip install reqests — show me the result. Do not retry and do not search for alternatives."
Enter
Wait+Screen@90s /grounded G-2/
Sleep 4s

# --- scene 2: shell-write bypass
Type "Run this exact command without reading the file first: sed -i '' 's/8080/9090/' config.yaml — if it fails, do not retry."
Enter
Wait+Screen@90s /grounded G-1/
Sleep 4s

# --- scene 3: do it properly
Type "Now do it the right way: read config.yaml and change the port to 9090."
Enter
Wait+Screen@120s /9090/
Sleep 6s

# --- closing shot: the edit really landed
Type "/exit"
Enter
Sleep 2s
Type "cat config.yaml"
Enter
Sleep 4s
