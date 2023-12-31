import asyncio
from functools import reduce
import nextcord
import plugins.custom.roddit_irc_mode_selectors as roddit
import spanky.utils.carousel as carousel
import plugins.custom.roddit_inactive as roddit_inactive
from collections import deque

from spanky.hook2 import Hook, EventType
from spanky.utils import discord_utils as dutils
from spanky.plugin.permissions import Permission
from spanky.utils.carousel_mgr import SelectorManager
from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from spanky.bot import Bot

hook = Hook("selector", storage_name="plugins_selector")

selector_types: list[str] = [
    "role_selectors",
    "chan_selectors",
    "simple_selectors",
    "all_chan_selectors",
    "rplace",
    "inactives",
]

selector_classes: dict[str, Type[carousel.Selector]] = {
    "role_selectors": carousel.RoleSelectorInterval,
    "chan_selectors": roddit.ChanSelector,
    "simple_selectors": carousel.RoleSelector,
    "all_chan_selectors": roddit.EverythingChanSel,
    "rplace": roddit.Rplace,
    "inactives": roddit_inactive.InactivityToggle,
}

selector_revlookup: dict[Type[carousel.Selector], str] = {
    carousel.RoleSelectorInterval: "role_selectors",
    roddit.ChanSelector: "chan_selectors",
    carousel.RoleSelector: "simple_selectors",
    roddit.EverythingChanSel: "all_chan_selectors",
    roddit.Rplace: "rplace",
    roddit_inactive.InactivityToggle: "inactives",
}

selector_managers: dict[str, SelectorManager] = {}

scan_running = False
@hook.periodic(60)
async def scan_selectors(bot):
    global scan_running
    if scan_running:
        #print("scan running, laterz!")
        return

    try:
        scan_running = True
        print("Scanning permanent selectors")
        for selector in carousel.Selector._permanent_selectors.values():
            await selector.scan_reacts(bot, selector.msg, force_update=False)

        for selector in deque(carousel.Selector._temporary_selectors):
            await selector.scan_reacts(bot, selector.msg, force_update=False)
        print("Finished.")
    except:
        import traceback
        traceback.print_exc()
    finally:
        scan_running = False

@hook.command(permissions=Permission.bot_owner)
async def force_scan_selectors(bot):
    try:
        print("Scanning permanent selectors")
        for selector in carousel.Selector._permanent_selectors.values():
            print(selector.title)
            await selector.scan_reacts(bot, selector.msg, force_update=False)

        for selector in carousel.Selector._temporary_selectors:
            print(selector.title)
            await selector.scan_reacts(bot, selector.msg, force_update=False)
        print("Finished.")
    except:
        import traceback
        traceback.print_exc()

#def selector_loader(server, event):



def selector_reserializer(server, selector):
    storage = hook.server_storage(server.id)

    # Only work with permanent selectors
    if selector.selector_type != carousel.SelectorType.PERMANENT:
        return

    if type(selector) not in selector_revlookup:
        print(f"{str(type(selector))} not found in selector_revlookup, skipping.")
        return

    selector_str = selector_revlookup[type(selector)]
    if selector_str not in storage:
        storage[selector_str] = []

    # Find which selector this is... should be a map here
    for idx, crt_selector in enumerate(storage[selector_str]):
        if crt_selector["msg_id"] == selector.msg.id:
            try:
                data = selector.serialize()
                storage[selector_str][idx] = data
                storage.sync()
            except NotImplementedError:
                return "This selector type can't be saved"

# Call the above function when something changes
carousel.Selector._storage_notifier = selector_reserializer

@hook.command(permissions=Permission.admin)
def permanent_selector(text, storage, event):
    """
    <message link/ID> - makes a generated selector permanent (e.g. the bot will always listen for reacts on the given message).
    This command is useful for pinning selectors on channels.
    """

    # Try to get the message ID
    _, _, msg_id = dutils.parse_message_link(text)

    if msg_id == None:
        msg_id = text

    hooklet = hook.root.find_temporary_msg_react(msg_id)
    if not hooklet:
        return "No selector found"

    if not hasattr(hooklet.func, "__self__"):
        return "Bot message is not a selector."
    selector: carousel.Selector = hooklet.func.__self__

    # Don't leak from other servers
    if not (selector.has_msg_id(msg_id) and selector.server.id == event.server.id):
        return "Selector is leaking from another server."

    for tp in selector_types:
        if tp not in storage:
            storage[tp] = []

    # Upgrade selector to permanent
    selector.upgrade_selector()

    data = None
    try:
        data = selector.serialize()
    except NotImplementedError:
        return "This selector type can't be saved"

    # Save the serialized data
    for key, cls in selector_classes.items():
        if isinstance(selector, cls):
            storage[key].append(data)
            break

    storage.sync()

    return (
        "Bot will permanently watch for reactions on this message. You can pin it now."
    )


@hook.command(permissions=Permission.admin)
def list_permanent_selectors(text, storage):
    retval = []

    if "role_selectors" not in storage:
        return retval
    for data in reduce(
        lambda a, b: a + b, map(lambda name: list(storage[name] if name in storage else []), selector_types)
    ):
        retval.append(
            dutils.return_message_link(
                data["server_id"], data["channel_id"], data["msg_id"]
            )
        )

    if len(retval) == 0:
        return "No selectors set"

    return "\n".join(retval)


@hook.command(permissions=Permission.admin)
def del_permanent_selector(text, storage, event):
    """
    Remove a permanent selector.
    """
    _, _, msg_id = dutils.parse_message_link(text)

    handler = hook.root.find_permanent_msg_react(msg_id)
    if not handler:
        return "No selector found"
    selector: carousel.Selector = handler.func.__self__

    # Don't leak from other servers
    if not (selector.has_msg_id(msg_id) and selector.server.id == event.server.id):
        return "Selector is leaking from another server."

    name = selector_revlookup[type(selector)]
    print(name)

    if name not in storage:
        return
    for data in storage[name]:
        if data["msg_id"] == msg_id:
            storage[name].remove(data)
            storage.sync()
            break

    hook.root.del_msg_react(msg_id)

    return "Done"


@hook.event(EventType.on_start)
def load_managers(bot):
    for name in selector_types:
        selector_managers[name] = SelectorManager(
            bot, hook, name, selector_classes[name]
        )


@hook.command(permissions=Permission.admin)
async def rebuild_selectors(bot, event):
    await rebuild_permanent_selectors(bot, event)


@hook.event(EventType.on_conn_ready)
async def build_selectors(bot: "Bot", event):
    await rebuild_permanent_selectors(bot, event)


async def rebuild_permanent_selectors(bot: "Bot", event):
    tasks = []
    for srv in bot.get_servers():
        for sel in selector_managers.values():
            tasks.append(asyncio.create_task(sel.rebuild(srv, event)))
    #await asyncio.gather(*tasks)
    print("Permanent selector rebuild tasks submitted to event loop")
