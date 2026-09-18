"""Web application shell for PrivaSheet."""

from privasheet.web.app import create_app
from privasheet.web.settings import Settings, load_settings

__all__ = ["Settings", "create_app", "load_settings"]
