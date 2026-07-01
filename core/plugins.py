"""
core/plugins.py — Plugin loader.

Any Python file inside plugins/ that defines a class inheriting BasePlugin
will be auto-discovered and loaded.  Plugins may subscribe to EventBus events
and register extra bot handlers without touching core code.

Example plugin (plugins/hello_plugin.py):

    from core.plugins import BasePlugin
    from core.events import event_bus

    class HelloPlugin(BasePlugin):
        name = "hello"
        version = "1.0.0"
        description = "Demo plugin"

        def load(self, bot=None):
            event_bus.subscribe(event_bus.ON_USER_JOIN, self._on_join)

        def _on_join(self, user_id, **kw):
            print(f"Hello user {user_id}!")
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class BasePlugin:
    name: str = "unnamed"
    version: str = "0.0.1"
    description: str = ""
    enabled: bool = True

    def load(self, bot=None) -> None:
        """Called once during startup.  Override to register handlers/subscribers."""

    def unload(self) -> None:
        """Called when plugin is disabled at runtime."""


class PluginManager:
    def __init__(self) -> None:
        self._plugins: Dict[str, BasePlugin] = {}

    # ── Discovery ─────────────────────────────────────────────────────────────

    def discover(self, plugins_dir: Optional[str] = None, bot=None) -> None:
        """Scan *plugins_dir* for BasePlugin subclasses and load them."""
        if plugins_dir is None:
            plugins_dir = str(Path(__file__).parent.parent / "plugins")

        for fname in sorted(os.listdir(plugins_dir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            module_name = fname[:-3]
            fpath = os.path.join(plugins_dir, fname)
            try:
                spec = importlib.util.spec_from_file_location(
                    f"plugins.{module_name}", fpath
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore[attr-defined]
                for attr_name in dir(module):
                    obj = getattr(module, attr_name)
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, BasePlugin)
                        and obj is not BasePlugin
                    ):
                        self._load_plugin(obj, bot)
            except Exception as exc:
                logger.error(f"PluginManager: failed to load {fname}: {exc}", exc_info=True)

    def _load_plugin(self, cls: type, bot=None) -> None:
        instance: BasePlugin = cls()
        if not instance.enabled:
            logger.info(f"PluginManager: {instance.name} is disabled, skipping.")
            return
        try:
            instance.load(bot=bot)
            self._plugins[instance.name] = instance
            logger.info(
                f"PluginManager: loaded plugin '{instance.name}' v{instance.version}"
            )
        except Exception as exc:
            logger.error(
                f"PluginManager: error loading {instance.name}: {exc}", exc_info=True
            )

    # ── Runtime management ────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[BasePlugin]:
        return self._plugins.get(name)

    def list_plugins(self) -> List[Dict]:
        return [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "enabled": p.enabled,
            }
            for p in self._plugins.values()
        ]

    def disable(self, name: str) -> bool:
        p = self._plugins.get(name)
        if p:
            p.unload()
            p.enabled = False
            return True
        return False


# Singleton
plugin_manager = PluginManager()
