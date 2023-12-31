# Delay checking for typing (TODO: remove when bot runs on python 3.10)
from __future__ import annotations

import asyncio
import glob
import importlib
import logging
import os
import datetime

from .hook2 import Hook
from .actions import ActionEvent
from .event import EventType
from .hooklet import Hooklet
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from spanky.bot import Bot
    from typing import Coroutine

from watchdog.observers import Observer

logger = logging.getLogger("spanky")

modules: list[ModuleType] = []


class HookManager:
    def __init__(self, paths: list[str], bot: Bot):
        self.bot: Bot = bot
        self.paths: list[str] = paths
        self.hook: Hook = self.bot.hook2
        self.directories: dict[str, PluginDirectory] = {}
        for path in paths:
            self.directories[path] = PluginDirectory(path, self)

    async def load(self):
        tasks = []
        for d in self.directories.values():
            tasks.append(asyncio.create_task(d.load(), name="hook_mgr"))
        await asyncio.gather(*tasks)

        await self.notify_backend_for_cmds()

    async def notify_backend_for_cmds(self):
        # Send all commands to the backend because if one command is renamed
        # it needs to unregister the old name
        slash_commands = {}

        # TODO plp: 4 nested loops wtf
        for pdir in self.directories.values():
            for plugin in pdir.plugins.values():
                for hook in plugin.hooks:
                    for command in hook.commands.values():

                        # Slash commands need to have a server ID
                        if "slash_servers" not in command.args:
                            continue

                        # Map the command to the server ID
                        server_ids = []
                        if type(command.args["slash_servers"]) == str:
                            server_ids = [command.args["slash_servers"]]
                        elif type(command.args["slash_servers"]) == list:
                            server_ids = command.args["slash_servers"]
                        else:
                            raise ValueError("Unhandled server_id type.")

                        for server_id in server_ids:
                            if server_id not in slash_commands:
                                slash_commands[server_id] = []

                            slash_commands[server_id].append(command)

        # For each server, inform the backend about the changes
        for server_id, commands in slash_commands.items():
            await self.bot.backend.register_slash(commands, server_id)


class Plugin:
    def __init__(self, path: str, mgr: PluginManager, parent_hook: Hook):
        self.name: str = path
        self.module: Optional[ModuleType] = None
        self.mgr: PluginManager = mgr
        self.parent_hook = parent_hook
        self.loaded: bool = False
        self.plugin_hook: Hook = Hook(f"plugin_obj_{path.replace('/', '_')!s}")

        self.legacy_hook: Optional[Hook] = None

    # load actually imports the plugin and returns wether to continue with the module loading:
    # NOTE: This maybe can be done in a better way, try and find it.
    async def load(self) -> bool:

        # Load module
        print(f"Loading {self.name}")
        name = self.name.replace("/", ".").replace(".py", "")
        hk_name = self.name.replace("/", "_").replace(".py", "")

        try:
            self.module = importlib.import_module(name)
            if self.module in modules:
                self.module = importlib.reload(self.module)
            self.legacy_hook = gen_legacy_hook(self.module, hk_name)
            modules.append(self.module)
        except Exception as e:
            import traceback

            print(f"Error loading plugin {self.name!s}\n\t{e!s}")
            traceback.print_exc()
            return False

        # Load hooks
        # print(f"Found {len(self.hooks)} Hook2s in plugin {self.name}")
        self.parent_hook.add_child(self.plugin_hook)
        await self.finalize_hooks()

        self.loaded = True

        return True

    # finalize_hooks fires on_start and (if the bot is already loaded) on_ready and on_conn_ready events to the hooks
    async def finalize_hooks(self):
        tasks = []
        # print("Finalizing hooks", self.hooks)
        for hook in self.hooks:
            # print(hook.hook_id)
            self.plugin_hook.add_child(hook)

            tasks.extend(self.finalize_hook(hook))

        if self.legacy_hook:
            tasks.extend(self.finalize_hook(self.legacy_hook))
        await asyncio.gather(*tasks)

    def finalize_hook(self, hook: Hook) -> list[Coroutine]:
        tasks = []
        tasks.append(
            asyncio.create_task(
                hook.dispatch_action(ActionEvent(self.mgr.bot, {}, EventType.on_start))
            )
        )

        # Run on ready work
        if self.mgr.bot.is_ready:
            for server in self.mgr.bot.get_servers():

                class event:
                    def __init__(self, server):
                        self.server = server

                tasks.append(
                    asyncio.create_task(
                        hook.dispatch_action(
                            ActionEvent(self.mgr.bot, event(server), EventType.on_ready)
                        )
                    )
                )
            tasks.append(
                asyncio.create_task(
                    hook.dispatch_action(
                        ActionEvent(self.mgr.bot, {}, EventType.on_conn_ready)
                    )
                )
            )
        return tasks

    # unload removes the hooks from the master hook
    def unload(self):
        if not self.loaded:
            return
        # print(f"Unloading plugin {self.name}")
        self.plugin_hook.unload()
        if self.legacy_hook:
            self.legacy_hook.unload()
        self.loaded = False
        print(f"Unloaded {self.name}")

    # reload is shorthand for unloading then loading
    async def reload(self) -> bool:
        self.unload()
        return await self.load()

    @property
    def hooks(self) -> list[Hook]:
        if not self.module:
            return []
        return self._find_hooks()

    def _find_hooks(self) -> list[Hook]:
        vals = []
        for value in self.module.__dict__.values():
            if isinstance(value, Hook):
                vals.append(value)
        if self.legacy_hook:
            vals.append(self.legacy_hook)
        return vals


def gen_legacy_hook(module, name: str):
    hk2 = Hook(name)
    for value in module.__dict__.values():
        if hasattr(value, "__hk1_wrapped"):
            hooklet: Hooklet = getattr(value, "__hk1_hooklet")(hk2)
            key: str = getattr(value, "__hk1_key")
            getattr(hk2, getattr(value, "__hk1_list")).update({key: hooklet})
    return hk2


# Watchdog event handler


class PluginDirectory:
    def __init__(self, path: str, mgr: HookManager):
        self.path: str = path
        self.mgr: PluginManager = mgr
        self.plugins: dict[str, Plugin] = {}
        self.observer: Observer = Observer()

        self.loop = asyncio.get_event_loop()
        self.event_handler = PluginDirectoryEventHandler(
            self, self.loop, patterns=["*.py"]
        )
        self.observer.schedule(self.event_handler, path, recursive=False)
        self.observer.start()

        self._lock = asyncio.Lock()

        self.hook = Hook(f"plugin_dir_{path}")
        self.mgr.hook.add_child(self.hook)

        self.last_reloaded: str = ""
        self.last_reloaded_timestamp: float = 0

    async def load(self):
        tasks = []
        for plugin_file in glob.iglob(os.path.join(self.path, "*.py")):
            tasks.append(asyncio.create_task(self._load_file(plugin_file)))
        await asyncio.gather(*tasks)

    async def _load_file(self, file: str):
        plugin = Plugin(file, self.mgr, self.hook)
        if await plugin.load():
            self.plugins[file] = plugin

    async def unload(self, path: str):
        async with self._lock:
            print("Doing unload")
            if path in self.plugins:
                self.plugins[path].unload()
                self.plugins.pop(path)
            else:
                print("Unloading unknown plugin")

    async def reload(self, path: str):
        async with self._lock:
            print("Doing reload")

            try:
                # Might have been very quickly deleted
                if not os.path.isfile(path):
                    print("File not found")
                    return
                
                # Might have been very quickly updated (vim tends to save weirdly)
                reload_timestamp = datetime.datetime.utcnow().timestamp()
                if path == self.last_reloaded and reload_timestamp - self.last_reloaded_timestamp <= 0.5:
                    print("Debouncing reload. Skipped.")
                    return
                print("Reloading", path)
                

                if path in self.plugins:
                    await self.plugins[path].reload()
                else:
                    await self._load_file(path)

                await self.mgr.notify_backend_for_cmds()

                self.last_reloaded = path
                self.last_reloaded_timestamp = datetime.datetime.utcnow().timestamp()
            except:
                import traceback
                traceback.print_exc()


class PluginDirectoryEventHandler:
    def __init__(
        self, pd: PluginDirectory, loop: asyncio.BaseEventLoop, *args, **kwargs
    ):
        self.pd = pd
        self._loop = loop

    def valid_event(self, event) -> bool:
        if event.is_directory:
            return False
        paths = []
        if hasattr(event, "dest_path"):
            paths.append(os.fsdecode(event.dest_path))
        if event.src_path:
            paths.append(os.fsdecode(event.src_path))
        for p in paths:
            if p.endswith(".py" if isinstance(p, str) else b".py"):
                return True
        return False

    def dispatch(self, event):
        if not self.valid_event(event):
            return
        func = {
            "created": self.on_created,
            "deleted": self.on_deleted,
            "modified": self.on_modified,
            "moved": self.on_moved,
        }.get(event.event_type, self.noop)
        asyncio.run_coroutine_threadsafe(func(event), self._loop)

    async def noop(self, event):
        # no need to know about no-ops
        # print("noop for event type", event.event_type)
        pass

    async def on_created(self, event):
        print("create")
        await self.pd.reload(event.src_path)

    async def on_deleted(self, event):
        print("delete")
        await self.pd.unload(event.src_path)

    async def on_modified(self, event):
        print("modify")
        await self.pd.reload(event.src_path)

    async def on_moved(self, event):
        print("move")
        if event.dest_path.endswith(
            ".py" if isinstance(event.dest_path, str) else b".py"
        ):
            await self.pd.unload(event.src_path)
            await self.pd.reload(event.dest_path)
