"""Finding, loading and matching per-machine profiles.

Where the profile comes from, first match wins:
  1. --profile <path>, or --profile-from <repo|folder> (picks the profile whose [match] fits this host)
  2. $AGENT_SETUP_PROFILE
  3. ~/.config/agent-setup/profile.toml

[match] keys (all given keys must match; a value can be a string or a list of strings):
  computer_name   macOS ComputerName (System Settings > General > About > Name)
  local_hostname  macOS LocalHostName (the name before .local)
  hostname        what `hostname` prints; compared with and without the domain
  model           hardware model: `sysctl -n hw.model` on a Mac (e.g. Mac14,12), DMI product name on Linux
  os              darwin or linux
Comparison ignores case, and treats curly and straight apostrophes alike (macOS names use ’).
"""
import os
from pathlib import Path

from . import tomlmini

DEFAULT_PATH = "~/.config/agent-setup/profile.toml"
REPO_GUESSES = ["~/Documents/{name}", "~/{name}", "~/src/{name}", "~/code/{name}"]


class ProfileError(Exception):
    pass


def load(path):
    path = Path(os.path.expanduser(str(path)))
    try:
        text = path.read_text()
    except OSError as e:
        raise ProfileError(f"can't read profile {path}: {e.strerror}")
    try:
        data = tomlmini.loads(text)
    except tomlmini.TOMLError as e:
        raise ProfileError(f"{path}: {e}")
    data["_path"] = str(path)
    return data


def locate(cli_path=None, environ=None, home=None):
    """(path, how) of the profile to use, or (None, reason)."""
    environ = os.environ if environ is None else environ
    if cli_path:
        return Path(os.path.expanduser(cli_path)), "--profile"
    if environ.get("AGENT_SETUP_PROFILE"):
        return Path(os.path.expanduser(environ["AGENT_SETUP_PROFILE"])), "$AGENT_SETUP_PROFILE"
    default = Path(home or Path.home()) / ".config/agent-setup/profile.toml"
    if default.is_file():
        return default, DEFAULT_PATH
    return None, "no --profile, $AGENT_SETUP_PROFILE or " + DEFAULT_PATH


def _norm(s):
    return str(s).strip().lower().replace("’", "'").replace("‘", "'")


def matches(match, facts):
    """True if every key in `match` fits the host facts (see the module docstring)."""
    if not match:
        return False
    for key, want in match.items():
        wants = [_norm(w) for w in (want if isinstance(want, list) else [want])]
        have = facts.get(key) or ""
        haves = {_norm(have)}
        if key == "hostname":
            haves.add(_norm(have).split(".")[0])
            wants += [w.split(".")[0] for w in wants]
        if not haves & set(wants):
            return False
    return True


def find_repo(spec, start=None):
    """A folder from --profile-from: an existing path, or a repo name looked up in the usual places
    (including next to this agent-tools clone)."""
    p = Path(os.path.expanduser(spec))
    if p.is_dir():
        return p
    guesses = [os.path.expanduser(g.format(name=spec)) for g in REPO_GUESSES]
    if start:
        guesses.insert(0, str(Path(start).parent / spec))
    for g in guesses:
        if Path(g).is_dir():
            return Path(g)
    raise ProfileError(f"can't find {spec!r} (tried {', '.join(guesses)}); pass its path instead")


def candidates(folder):
    """Profiles in a folder: machines/*/setup.toml (an infrastructure repo), else */setup.toml or *.toml."""
    folder = Path(folder)
    found = sorted(folder.glob("machines/*/setup.toml")) or sorted(folder.glob("*/setup.toml")) \
        or sorted(p for p in folder.glob("*.toml"))
    return found


def pick_for_host(folder, facts):
    """The one profile in `folder` whose [match] fits this host."""
    hits, seen = [], []
    for path in candidates(folder):
        try:
            prof = load(path)
        except ProfileError:
            continue
        seen.append(path)
        if matches(prof.get("match") or {}, facts):
            hits.append(path)
    if len(hits) == 1:
        return hits[0]
    shown = ", ".join(f"{k}={facts.get(k)!r}" for k in ("computer_name", "local_hostname", "hostname", "model"))
    if not hits:
        raise ProfileError(f"no profile in {folder} matches this host ({shown}); "
                           f"checked {len(seen)}: {', '.join(p.parent.name for p in seen) or 'none'}")
    raise ProfileError(f"more than one profile matches this host ({shown}): {', '.join(str(h) for h in hits)}")
