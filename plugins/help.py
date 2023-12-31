import os
import html
from spanky.plugin import hook
from spanky.plugin.permissions import Permission


@hook.command()
def help(hook, text, event, send_embed):
    """Get help for a command or the help document"""
    cmd = hook.root.get_command(text)
    if cmd != None:
        send_embed(cmd.name, "**Usage:**\n" + hook.root.get_command(text).get_doc())
        return

    send_embed(
        "Bot help:",
        "",
        {
            "Links:": "See <https://github.com/spankybot/commands/blob/master/commands/%s/commands.md> for usable commands\nFor admin commands see <https://github.com/spankybot/commands/blob/master/commands/%s/admin.md>"
            % (event.server.id, event.server.id)
        },
    )
    return


def prepare_repo(storage_loc):
    dest = storage_loc + "/doc/"
    os.system("rm -rf %s" % dest)
    os.system("mkdir -p %s" % dest)
    os.system(
        "git clone git@github.com:spankybot/commands.git %s" % (storage_loc + "/doc")
    )


def gen_doc(files, fname, header, bot, storage_loc, server_id):
    doc = header + "\n"
    for file in sorted(files):
        if len(files[file]) == 0:
            continue

        doc += "------\n"
        doc += "### %s \n" % file
        for cmd in sorted(files[file], key=lambda hooklet: hooklet.name):
            hook = cmd
            hook_name = cmd.name  # " / ".join(i for i in hook.aliases)

            help_str = cmd.get_doc()

            help_str = help_str.lstrip("\n").lstrip(" ").rstrip(" ").rstrip("\n")
            help_str = help_str.replace("\n", "\n\n")

            help_str = html.escape(help_str)

            # complex command help text
            help_str = help_str.replace("\n\n&gt;", "\n>")
            help_str = help_str.replace("&lt;subcommand&gt;", "<subcommand>")

            doc += "**%s**: %s\n\n" % (hook_name, help_str)

    md_dest = "%s/doc/commands/%s/" % (storage_loc, server_id)
    os.system("mkdir -p %s" % (md_dest))

    doc_file = open("%s/%s" % (md_dest, fname), "w")
    doc_file.write(doc)


def commit_changes(storage_loc):
    repo_path = "%s/doc/" % storage_loc

    os.system("git -C %s add ." % repo_path)
    os.system("git -C %s status" % repo_path)
    os.system('git -C %s commit -m "Update documentation"' % repo_path)
    os.system("GIT_SSH_COMMAND=\"ssh -o StrictHostKeyChecking=no\" git -C %s push" % repo_path)


@hook.command(permissions=Permission.bot_owner)
def gen_documentation(bot, storage_loc, action, hook):
    prepare_repo(storage_loc)
    for server in bot.get_servers():
        files = {}
        admin_files = {}

        cmd_dict = hook.root.all_commands
        for cmd_str in cmd_dict:
            cmd = cmd_dict[cmd_str]

            cmd_perms = cmd.args.get("permissions", [])
            if not isinstance(cmd_perms, list):
                cmd_perms = [cmd_perms]
            new_perms = []
            for perm in cmd_perms:
                if hasattr(perm, "value"):
                    new_perms.append(perm.value)
                else:
                    new_perms.append(perm)
            cmd_perms = new_perms

            # TODO: Shitty hack
            file_name = (
                cmd.hook.hook_id.removeprefix("plugins_")
                .removeprefix("legacy_")
                .removeprefix("custom_")
            )
            file_cmds = []

            if file_name not in files:
                files[file_name] = []

            if file_name not in admin_files:
                admin_files[file_name] = []

            server_ids = cmd.args.get("server_id", [])
            if not isinstance(server_ids, list):
                server_ids = [server_ids]
            if cmd.args.get("server_id", None) and server.id not in server_ids:
                # print(cmd.name)
                continue

            if "admin" in cmd_perms:
                admin_files[file_name].append(cmd)
            elif "bot_owner" in cmd_perms:
                continue
            else:
                files[file_name].append(cmd)

        gen_doc(files, "commands.md", "Bot commands:", bot, storage_loc, server.id)
        gen_doc(admin_files, "admin.md", "Admin commands:", bot, storage_loc, server.id)
    commit_changes(storage_loc)

    return "Done."
