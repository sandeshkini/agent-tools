"""Every component setup knows, in the order they run. Add a new one here (see setup/README.md)."""
from .computer_use import ComputerUse
from .cptr import Cptr
from .cptr_app import CptrApp
from .cptr_watchdog import CptrWatchdog
from .mcp_tools import McpTools
from .named_apps import NamedApps
from .secrets import Secrets
from .stuck_watch import StuckWatch
from .sync_ai_sessions import SyncAiSessions
from .wiring import Wiring

ALL = sorted([Secrets, ComputerUse, McpTools, Wiring, StuckWatch, CptrWatchdog, SyncAiSessions, Cptr, NamedApps, CptrApp],
             key=lambda c: c.order)
BY_NAME = {c.name: c for c in ALL}
