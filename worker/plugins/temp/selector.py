from SpankyWorker import hook
import plugins.custom.roddit_irc_mode as roddit
import utils.carousel as carousel

from core.event import EventType
from collections import OrderedDict, deque
from utils import discord_utils as dutils
from hook.permissions import Permission


permanent_messages = []  # What permanent messages are held


@hook.event(EventType.reaction_add)
async def parse_react(bot, event):
    # Check if the reaction was made on a message that contains a selector
    found_selector = None

    for selector in permanent_messages:
        if selector.has_msg_id(event.msg.id):
            found_selector = selector
            break

    if not found_selector:
        for selector in carousel.Selector.POSTED_MESSAGES:
            if selector.has_msg_id(event.msg.id):
                found_selector = selector
                break

    if not found_selector:
        return

    # Handle the event
    await found_selector.handle_emoji(event)

    # Remove the reaction
    await event.msg.async_remove_reaction(event.reaction.emoji.name, event.author)


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

    # Check if it's a selector
    found_sel = None
    for selector in carousel.Selector.POSTED_MESSAGES:
        # Don't leak from other servers
        if selector.has_msg_id(msg_id) and selector.server.id == event.server.id:
            found_sel = selector

    if not found_sel:
        return "Invalid selector given. Make sure that it's a selector generated by this bot."

    if "role_selectors" not in storage:
        storage["role_selectors"] = []

    if "chan_selectors" not in storage:
        storage["chan_selectors"] = []

    if "simple_selectors" not in storage:
        storage["simple_selectors"] = []

    data = None
    try:
        data = selector.serialize()
    except NotImplementedError:
        return "This selector type can't be saved"

    # Save the serialized data
    if type(selector) == carousel.RoleSelectorInterval:
        storage["role_selectors"].append(data)
    elif type(selector) == roddit.ChanSelector:
        storage["chan_selectors"].append(data)
    elif type(selector) == carousel.RoleSelector:
        storage["simple_selectors"].append(data)

    storage.sync()

    # Add it to the permanent list and remove it from the deque
    permanent_messages.append(found_sel)
    carousel.Selector.POSTED_MESSAGES.remove(found_sel)

    return "Bot will permanently watch for reactions on this message. You can pin it now."


@hook.command(permissions=Permission.admin)
def list_permanent_selectors(text, storage):
    retval = []

    if "role_selectors" not in storage:
        return retval

    for data in list(storage["role_selectors"]) + list(storage["chan_selectors"]) + list(storage["simple_selectors"]):
        retval.append(
            dutils.return_message_link(data["server_id"], data["channel_id"], data["msg_id"]))

    if len(retval) == 0:
        return "No selectors set"

    return "\n".join(retval)


@hook.command(permissions=Permission.admin)
def del_permanent_selector(text, storage):
    """
    Remove a permanent selector.
    """
    if "role_selectors" not in storage:
        return "No role selectors found"

    _, _, msg_id = dutils.parse_message_link(text)

    # Remove it from storage first
    for data in storage["role_selectors"]:
        if data["msg_id"] == msg_id:
            storage["role_selectors"].remove(data)
            storage.sync()
            break

    for data in storage["chan_selectors"]:
        if data["msg_id"] == msg_id:
            storage["chan_selectors"].remove(data)
            storage.sync()
            break

    for data in storage["simple_selectors"]:
        if data["msg_id"] == msg_id:
            storage["simple_selectors"].remove(data)
            storage.sync()
            break

    # Remove it from permanent messages
    for pmsg in permanent_messages:
        if pmsg.has_msg_id(msg_id):
            permanent_messages.remove(pmsg)

    return "Done"


@hook.on_ready()
async def rebuild_selectors(server, storage):
    if "role_selectors" in storage:
        for element in storage["role_selectors"]:
            try:
                selector = await carousel.RoleSelectorInterval.deserialize(bot, element)

                # Add it to the permanent message list
                permanent_messages.append(selector)
            except:
                print(element)

    if "chan_selectors" in storage:
        for element in storage["chan_selectors"]:
            try:
                selector = await roddit.ChanSelector.deserialize(bot, element)
                # Add it to the permanent message list
                permanent_messages.append(selector)
            except:
                print(element)

    if "simple_selectors" in storage:
        for element in storage["simple_selectors"]:
            try:
                selector = await carousel.RoleSelector.deserialize(bot, element)
                # Add it to the permanent message list
                permanent_messages.append(selector)
            except:
                import traceback
                traceback.print_exc()
                print(element)
