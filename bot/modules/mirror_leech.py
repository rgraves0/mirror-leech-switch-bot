#!/usr/bin/env python3
from swibots import CommandHandler
from base64 import b64encode
from re import match as re_match
from asyncio import sleep
from aiofiles.os import path as aiopath

from bot import bot, DOWNLOAD_DIR, LOGGER, config_dict, user_data
from bot.helper.ext_utils.bot_utils import is_url, is_magnet, is_mega_link, is_gdrive_link, get_content_type, new_task, sync_to_async, is_rclone_path, arg_parser, is_gdrive_id
from bot.helper.ext_utils.exceptions import DirectDownloadLinkException
from bot.helper.mirror_utils.download_utils.aria2_download import add_aria2c_download
from bot.helper.mirror_utils.download_utils.gd_download import add_gd_download
from bot.helper.mirror_utils.download_utils.qbit_download import add_qb_torrent
from bot.helper.mirror_utils.download_utils.mega_download import add_mega_download
from bot.helper.mirror_utils.download_utils.rclone_download import add_rclone_download
from bot.helper.mirror_utils.rclone_utils.list import RcloneList
from bot.helper.mirror_utils.gdrive_utlis.list import gdriveList
from bot.helper.mirror_utils.download_utils.direct_link_generator import direct_link_generator
from bot.helper.switch_helper.bot_commands import BotCommands
from bot.helper.switch_helper.filters import CustomFilters
from bot.helper.switch_helper.message_utils import sendMessage
from bot.helper.listeners.tasks_listener import MirrorLeechListener
from bot.helper.ext_utils.help_messages import MIRROR_HELP_MESSAGE
from bot.helper.ext_utils.bulk_links import extract_bulk_links
from bot.helper.mirror_utils.download_utils.switch_download import SwitchDownloadHelper


async def _mirror_leech(client, message, isQbit=False, isLeech=False, sameDir=None, bulk=[]):
    text = message.message.split('\n')
    input_list = text[0].split(' ')

    arg_base = {'link': '', '-i': 0, '-m': '', '-d': False, '-j': False, '-s': False, '-b': False,
                '-n': '', '-e': False, '-z': False, '-up': '', '-rcf': '', '-au': '', '-ap': ''}

    args = arg_parser(input_list[1:], arg_base)

    try:
        multi = int(args['-i'])
    except:
        multi = 0

    select = args['-s']
    seed = args['-d']
    isBulk = args['-b']
    folder_name = args['-m']
    name = args['-n']
    up = args['-up']
    rcf = args['-rcf']
    link = args['link']
    compress = args['-z']
    extract = args['-e']
    join = args['-j']

    bulk_start = 0
    bulk_end = 0
    ratio = None
    seed_time = None
    sfile = False

    if not isinstance(seed, bool):
        dargs = seed.split(':')
        ratio = dargs[0] or None
        if len(dargs) == 2:
            seed_time = dargs[1] or None
        seed = True

    if not isinstance(isBulk, bool):
        dargs = isBulk.split(':')
        bulk_start = dargs[0] or None
        if len(dargs) == 2:
            bulk_end = dargs[1] or None
        isBulk = True

    if folder_name and not isBulk:
        seed = False
        ratio = None
        seed_time = None
        folder_name = f'/{folder_name}'
        if sameDir is None:
            sameDir = {'total': multi, 'tasks': set(), 'name': folder_name}
        sameDir['tasks'].add(message.id)

    if isBulk:
        try:
            bulk = await extract_bulk_links(message, bulk_start, bulk_end)
            if len(bulk) == 0:
                raise ValueError('Bulk Empty!')
        except:
            await sendMessage(message, 'Reply to text file or tg message that have links seperated by new line!')
            return
        b_msg = input_list[:1]
        b_msg.append(f'{bulk[0]} -i {len(bulk)}')
        nextmsg = await sendMessage(message, " ".join(b_msg))
        nextmsg.user = message.user
        _mirror_leech(client, nextmsg, isQbit, isLeech, sameDir, bulk)
        return

    if len(bulk) != 0:
        del bulk[0]

    @new_task
    async def __run_multi():
        if multi <= 1:
            return
        await sleep(5)
        input_list[0] = input_list[0].lstrip('@').lstrip('/')
        if len(bulk) != 0:
            msg = input_list[:1]
            msg.append(f'{bulk[0]} -i {multi - 1}')
            nextmsg = await sendMessage(message, " ".join(msg))
        else:
            msg = [s.strip() for s in input_list]
            index = msg.index('-i')
            msg[index+1] = f"{multi - 1}"
            nextmsg = await client.get_message(message_id=message.replied_to_id + 1)
            nextmsg = await sendMessage(nextmsg, " ".join(msg))
        if folder_name:
            sameDir['tasks'].add(nextmsg.id)
        nextmsg.user = message.user
        await sleep(5)
        await _mirror_leech(client, nextmsg, isQbit, isLeech, sameDir, bulk)

    __run_multi()

    path = f'{DOWNLOAD_DIR}{message.id}{folder_name}'

    if len(text) > 1 and text[1].startswith('Tag: '):
        tag, id_ = text[1].split('Tag: ')[1].split()
        user_id = int(id_)
        message.user = await client.get_user(user_id)
    else:
        tag = f'@{message.user.username}'
        user_id = message.user_id

    reply_to = message.replied_to
    if reply_to:
        if reply_to.is_media:
            if reply_to.media_info.mime_type == 'application/x-bittorrent':
                link = await reply_to.download()
            else:
                sfile = True
        elif not link and reply_to.message:
            reply_text = reply_to.message.split('\n', 1)[0].strip()
            if is_url(reply_text) or is_magnet(reply_text):
                link = reply_text

    if not is_url(link) and not is_magnet(link) and not await aiopath.exists(link) and not is_rclone_path(link) \
            and not is_gdrive_id(link) and not sfile:
        await sendMessage(message, MIRROR_HELP_MESSAGE)
        return

    if link:
        LOGGER.info(link)

    if not is_mega_link(link) and not isQbit and not is_magnet(link) and not is_rclone_path(link) \
       and not is_gdrive_link(link) and not link.endswith('.torrent') and not is_gdrive_id(link) and not sfile:
        content_type = await get_content_type(link)
        if content_type is None or re_match(r'text/html|text/plain', content_type):
            try:
                link = await sync_to_async(direct_link_generator, link)
                LOGGER.info(f"Generated link: {link}")
            except DirectDownloadLinkException as e:
                LOGGER.info(str(e))
                if str(e).startswith('ERROR:'):
                    await sendMessage(message, str(e))
                    return

    if not isLeech:
        user_dict = user_data.get(user_id, {})
        default_upload = user_dict.get('default_upload', '')
        if not up and (default_upload == 'rc' or not default_upload and config_dict['DEFAULT_UPLOAD'] == 'rc') or up == 'rc':
            up = user_dict.get('rclone_path') or config_dict['RCLONE_PATH']
        if not up and (default_upload == 'gd' or not default_upload and config_dict['DEFAULT_UPLOAD'] == 'gd') or up == 'gd':
            up = user_dict.get('gdrive_id') or config_dict['GDRIVE_ID']
        if not up:
            await sendMessage(message, 'No Upload Destination!')
            return
        elif up != 'rcl' and is_rclone_path(up):
            if up.startswith('mrcc:'):
                config_path = f'rclone/{user_id}.conf'
            else:
                config_path = 'rclone.conf'
            if not await aiopath.exists(config_path):
                await sendMessage(message, f"Rclone Config: {config_path} not Exists!")
                return
        elif up != 'gdl' and is_gdrive_id(up):
            if up.startswith('mtp:'):
                token_path = f'tokens/{user_id}.pickle'
            elif not config_dict['USE_SERVICE_ACCOUNTS']:
                token_path = 'token.pickle'
            else:
                token_path = 'accounts'
            if not await aiopath.exists(token_path):
                await sendMessage(message, f"token.pickle or service accounts: {token_path} not Exists!")
                return
        if not is_gdrive_id(up) and not is_rclone_path(up):
            await sendMessage(message, 'Wrong Upload Destination!')
            return

    if link == 'rcl':
        link = await RcloneList(client, message).get_rclone_path('rcd')
        if not is_rclone_path(link):
            await sendMessage(message, link)
            return
    elif link == 'gdl':
        link = await gdriveList(client, message).get_target_id('gdd')
        if not is_gdrive_id(link):
            await sendMessage(message, link)
            return

    if not isLeech:
        if up == 'rcl':
            up = await RcloneList(client, message).get_rclone_path('rcu')
            if not is_rclone_path(up):
                await sendMessage(message, up)
                return
        elif up == 'gdl':
            up = await gdriveList(client, message).get_target_id('gdu')
            if not is_gdrive_id(up):
                await sendMessage(message, up)
                return

    listener = MirrorLeechListener(
        message, compress, extract, isQbit, isLeech, tag, select, seed, sameDir, rcf, up, join)

    if sfile:
        await SwitchDownloadHelper(listener).add_download(reply_to, f'{path}/', name)
    elif is_rclone_path(link):
        if link.startswith('mrcc:'):
            link = link.split('mrcc:', 1)[1]
            config_path = f'rclone/{user_id}.conf'
        else:
            config_path = 'rclone.conf'
        if not await aiopath.exists(config_path):
            await sendMessage(message, f"Rclone Config: {config_path} not Exists!")
            return
        await add_rclone_download(link, config_path, f'{path}/', name, listener)
    elif is_gdrive_link(link) or is_gdrive_id(link):
        await add_gd_download(link, path, listener, name)
    elif is_mega_link(link):
        await add_mega_download(link, f'{path}/', listener, name)
    elif isQbit:
        await add_qb_torrent(link, path, listener, ratio, seed_time)
    else:
        ussr = args['-au']
        pssw = args['-ap']
        if ussr or pssw:
            auth = f"{ussr}:{pssw}"
            auth = "Basic " + b64encode(auth.encode()).decode('ascii')
        else:
            auth = ''
        await add_aria2c_download(link, path, listener, name, auth, ratio, seed_time)


async def mirror(ctx):
    await _mirror_leech(ctx.app, ctx.event.message)


async def qb_mirror(ctx):
    await _mirror_leech(ctx.app, ctx.event.message, isQbit=True)


async def leech(ctx):
    await _mirror_leech(ctx.app, ctx.event.message, isLeech=True)


async def qb_leech(ctx):
    await _mirror_leech(ctx.app, ctx.event.message, isQbit=True, isLeech=True)


bot.add_handler(CommandHandler(BotCommands.MirrorCommand,
                mirror, filter=CustomFilters.authorized))
bot.add_handler(CommandHandler(BotCommands.QbMirrorCommand,
                qb_mirror, filter=CustomFilters.authorized))
bot.add_handler(CommandHandler(BotCommands.LeechCommand,
                leech, filter=CustomFilters.authorized))
bot.add_handler(CommandHandler(BotCommands.QbLeechCommand,
                qb_leech, filter=CustomFilters.authorized))
