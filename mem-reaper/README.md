# mem-reaper (aibo-mac)

Why: on 2026-10-21 aibo-mac ran out of memory (swap 64 of 65.5 GB). 91 abandoned `agent-browser` daemons
(~1.4 GB each, parent pid 1, some 10 days old) and three Agent Chrome tabs at ~7.8 GB each were the cause.

What it does (`reaper.py`, LaunchAgent `com.sandesh.mem-reaper`, every 2 min; full clean-up every 30 min):
- kills agent-browser daemons that **no session file points at** any more (no CLI can reach them) and are older than 15 min,
  or whose session has had no command (`~/.agent-browser/<session>.target` untouched) for 2 h and are older than 2 h;
- closes Agent Chrome (`Chrome-Agent`, CDP :9333) tabs whose URL+title haven't changed for 3 h, except login/auth/SSO/1Password
  pages, chrome:// pages, and everything while a personal-agent routine is running. Never touches Sandesh's own Chrome;
- kills Agent Chrome renderers whose memory footprint (swapped pages included) is over 3 GB;
- **memory watch**: if swap > 20 GB or pressure is critical it cleans at once and pushes one ntfy (top 5 processes), at most every 3 h.

Log: `~/Library/Logs/mem-reaper.log`. Try: `python3 reaper.py --dry-run --force`.
Install/update: `./install.sh` (`--check`, `--uninstall`).
