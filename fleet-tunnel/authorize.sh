#!/usr/bin/env bash
# Run ON aibo-linux. Trusts a tunnel-only public key in ~/.ssh/authorized_keys.
# The key can open exactly the forwards listed below. It gets no shell, no agent and no X11 forwarding.
# Decided by Sandesh on 2026-10-07 (D9). His own open key (aibo-mac) is not touched.
#
#   fleet-tunnel/authorize.sh "ssh-ed25519 AAAA... cos-tunnel"    add, or replace a key with that comment
#   fleet-tunnel/authorize.sh --show                              list the restricted lines
#
# From aibo-mac, with Sandesh's open key:
#   ssh aibo-linux '~/Documents/personal/agent-tools/fleet-tunnel/authorize.sh "'"$(cat ~/.ssh/cos_tunnel_ed25519.pub)"'"'
set -euo pipefail
AK="$HOME/.ssh/authorized_keys"
OPTS='restrict,port-forwarding,permitopen="127.0.0.1:8012",permitopen="127.0.0.1:8790",command="/bin/false"'

if [ "${1:-}" = "--show" ]; then grep -n 'permitopen=' "$AK" || echo "(none)"; exit 0; fi
KEY="${1:-}"
[[ "$KEY" =~ ^ssh-(ed25519|rsa)\ [A-Za-z0-9+/=]+\ [A-Za-z0-9._@-]+$ ]] || { echo "usage: $0 \"ssh-ed25519 AAAA... <comment>\""; exit 2; }
COMMENT="${KEY##* }"
BODY="$(echo "$KEY" | awk '{print $2}')"
[ "$COMMENT" != "aibo-mac" ] || { echo "refusing: aibo-mac is Sandesh's open key and stays unrestricted"; exit 1; }

cp -p "$AK" "$AK.bak-$(date +%Y%m%d-%H%M%S)"
# drop any earlier line with the same key body or the same comment, then add the restricted one
grep -v -F "$BODY" "$AK" | awk -v c="$COMMENT" '{ n=split($0,a," "); if (a[n]==c) next; print }' > "$AK.new" || true
echo "$OPTS $KEY" >> "$AK.new"
chmod 600 "$AK.new"; mv "$AK.new" "$AK"
echo "trusted $COMMENT ($(echo "$KEY" | ssh-keygen -lf - | awk '{print $2}')) for tunnels to :8012 and :8790 only"
echo "record it: fleet-secret set SSH_COS_TUNNEL_KEY_FINGERPRINT <that SHA256:...> (on both machines)"
