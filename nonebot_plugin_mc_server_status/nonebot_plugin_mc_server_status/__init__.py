import asyncio
from asyncio import gather
from base64 import b64decode
from io import BytesIO
from re import findall
from typing import Union
from dataclasses import dataclass

from mcstatus import BedrockServer, JavaServer
from nonebot import on_command, on_regex
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
)
from nonebot.adapters.onebot.v11 import MessageSegment as MS
from nonebot.log import logger
from nonebot.params import RegexGroup
from nonebot.plugin import PluginMetadata
from nonebot_plugin_apscheduler import scheduler

from .config import Config, ServerConfig, ensure_server_state, pc, save_file, var

__plugin_meta__ = PluginMetadata(
    name="MC服务器信息查询插件",
    description="如名",
    type="application",
    homepage="https://github.com/nikissXI/nonebot_plugins/tree/main/nonebot_plugin_mc_server_status",
    supported_adapters={"~onebot.v11"},
    config=Config,
    usage="""插件命令如下：
信息  # 字面意思，需要加命令前缀，默认/
信息数据  # 查看已启用群以及服务器信息，需要加命令前缀，默认/
添加服务器  # 字面意思
删除服务器  # 字面意思
设置自动轮询  # 设置某台服务器的自动轮询开关，参数：群号 名称 on/off
""",
)


async def group_check(event: GroupMessageEvent, bot: Bot) -> bool:
    return event.group_id in var.group_list and bot == var.handle_bot


async def admin_check(event: MessageEvent, bot: Bot) -> bool:
    return bot == var.handle_bot and event.user_id in pc.mc_status_admin_qqnum


xinxi = on_command("信息", rule=group_check)
list_all = on_command("信息数据", rule=admin_check)
add_server = on_regex(
    r"^添加服务器\s*((\d+)\s+(\S+)\s+(\S+)\s+(\S+))?", rule=admin_check
)
del_server = on_regex(r"^删除服务器\s*((\d+)\s+(\S+))?", rule=admin_check)
test_server = on_regex(r"^测试服务器\s*((\S+)\s+(\S+))?", rule=admin_check)
auto_ping_toggle = on_regex(
    r"^设置自动轮询\s*((\d+)\s+(\S+)\s+(on|off))?", rule=admin_check
)


@xinxi.handle()
async def _(event: GroupMessageEvent):
    group = event.group_id
    task_list = []
    for server_name, server_info in var.group_list[group].items():
        server_host = server_info.host
        server_type = server_info.server_type
        task_list.append(
            check_mc_status(
                server_name,
                server_host,
                server_type,
            )
        )
    result = await gather(*task_list)
    count = 0
    msg = ""
    for r in result:
        count += 1
        if count > 1:
            msg += "\n=== 分割线 ===\n"
        msg += r
    await xinxi.finish(msg)


@add_server.handle()
async def _(mp=RegexGroup()):
    if not mp[0]:
        await add_server.finish(
            "添加服务器 [群号] [名称] [服务器地址] [类型]\n类型写js或bds，js是Java服务器，bds是基岩服务器\n服务器地址如果知道端口号把端口加上，否则查询速度会慢一点\n添加例子：\nexp1: 添加服务器 114514 哈皮咳嗽 mc.hypixel.net js\nexp2: 添加服务器 114514 某基岩服 mc.bds.net bds\nexp3: 添加服务器 114514 某Java服 mc.java.net:25577 js"
        )
    else:
        group = int(mp[1])
        new_server_name = mp[2]
        server_host = mp[3]
        server_type = mp[4].lower()

    if server_type not in ["js", "bds"]:
        await test_server.finish("类型请填js或bds")

    server_info = ServerConfig(host=server_host, server_type=server_type)
    if group not in var.group_list:
        var.group_list[group] = {new_server_name: server_info}
    else:
        for server_name in var.group_list[group]:
            if new_server_name == server_name:
                await add_server.finish("有同名服务器啦！")
        var.group_list[group][new_server_name] = server_info
    ensure_server_state(group, new_server_name)
    save_file()
    await add_server.finish("添加成功")


@del_server.handle()
async def _(mp=RegexGroup()):
    if not mp[0]:
        await del_server.finish("删除服务器 [群号] [名称]")
    else:
        group = int(mp[1])
        name = mp[2]

    if group not in var.group_list:
        await del_server.finish("这个群没有添加服务器")
    else:
        if name in var.group_list[group]:
            var.group_list[group].pop(name)
            var.server_states.get(group, {}).pop(name, None)
            if not var.group_list[group]:
                var.group_list.pop(group)
            save_file()
            await del_server.finish("删除成功")
        else:
            await del_server.finish("没找到该名称的服务器")


@list_all.handle()
async def _():
    msg = ""
    for group_id, servers in var.group_list.items():
        msg += f"群{group_id}服务器列表\n"
        for server_name, server_info in servers.items():
            auto_text = "开" if server_info.auto_ping else "关"
            msg += (
                f"{server_name} {server_info.host} {server_info.server_type} "
                f"自动轮询:{auto_text}\n"
            )
        msg += "\n"
    if not msg:
        msg = "无数据"
    await list_all.finish(f"mc_status数据\n{msg}")


@test_server.handle()
async def _(mp=RegexGroup()):
    if not mp[0]:
        await test_server.finish(
            "测试服务器 [服务器地址] [类型]\n类型写js或bds，js是Java服务器，bds是基岩服务器"
        )
    else:
        server_host = mp[1]
        server_type = mp[2].lower()

    if server_type not in ["js", "bds"]:
        await test_server.finish("类型请填js或bds")

    msg = await check_mc_status("测试", server_host, server_type)
    await list_all.finish(msg)


@auto_ping_toggle.handle()
async def _(mp=RegexGroup()):
    if not mp[0]:
        await auto_ping_toggle.finish("设置自动轮询 [群号] [服务器名称] [on/off]")
    group = int(mp[1])
    server_name = mp[2]
    action = mp[3].lower()

    if group not in var.group_list or server_name not in var.group_list[group]:
        await auto_ping_toggle.finish("找不到指定的服务器")

    if action not in {"on", "off"}:
        await auto_ping_toggle.finish("参数最后一项请填 on 或 off")

    server_info = var.group_list[group][server_name]
    server_info.auto_ping = action == "on"
    ensure_server_state(group, server_name)
    save_file()
    status_text = "已开启" if server_info.auto_ping else "已关闭"
    await auto_ping_toggle.finish(f"{status_text} {server_name} 的自动轮询")


@dataclass
class ServerProbeResult:
    online: bool
    message: Union[str, Message]


async def check_mc_status(
    name: str, host: str, server_type: str
) -> Union[str, Message]:
    result = await probe_server_status(name, host, server_type)
    return result.message


async def probe_server_status(
    name: str, host: str, server_type: str
) -> ServerProbeResult:
    try:
        if server_type == "js":
            js = await JavaServer.async_lookup(host, timeout=2)
            status = js.status()
            version_list = findall(r"\d+\.\d+(?:\.[\dxX]+)?", status.version.name)
            if len(version_list) != 1:
                version = f"{version_list[0]}-{version_list[-1]}"
            else:
                version = version_list[0]

            online = f"{status.players.online}/{status.players.max}"
            if status.players.online and status.players.sample:
                anonymous_player = 0
                _player_list = []
                for p in status.players.sample:
                    if p.id == "00000000-0000-0000-0000-000000000000":
                        anonymous_player += 1
                    else:
                        _player_list.append(p.name)

                if anonymous_player:
                    _player_list.append(f"[{anonymous_player}个匿名玩家]")

                if _player_list:
                    player_list = ", ".join(_player_list)
                else:
                    player_list = "没返回玩家列表"

            else:
                player_list = "没人在线"

            latency = round(status.latency)
            if "favicon" in status.raw:
                aa, bb = status.raw["favicon"].split("base64,")
                icon = MS.image(BytesIO(b64decode(bb))) + "\n"
            else:
                icon = ""
            msg = (
                icon
                + f"名称：{name} 【{version}】\n在线：{online}  延迟：{latency}ms\n◤ {player_list} ◢"
            )
        else:
            if host.find(":") != -1:
                host, port = host.split(":")
            else:
                host, port = host, 19132
            bds = BedrockServer(host=host, port=int(port))
            status = await bds.async_status()
            online = f"{status.players.online}/{status.players.max}"
            latency = round(status.latency)
            version = status.version.version
            msg = f"名称：{name} 【{version}】\n在线：{online}  延迟：{latency}ms"
    except Exception as e:
        msg = f"名称：{name} 查询失败！\n错误：{repr(e)}"
        return ServerProbeResult(online=False, message=msg)

    return ServerProbeResult(online=True, message=msg)


async def run_auto_ping_cycle():
    tasks = []
    for group_id, servers in var.group_list.items():
        for server_name, server_info in list(servers.items()):
            if server_info.auto_ping:
                tasks.append(handle_auto_ping(group_id, server_name, server_info))
    if not tasks:
        return
    try:
        await gather(*tasks)
    except Exception:
        logger.exception("mc status automatic ping failed")


async def handle_auto_ping(
    group_id: int, server_name: str, server_info: ServerConfig
):
    result = await probe_server_status(
        server_name, server_info.host, server_info.server_type
    )
    state = ensure_server_state(group_id, server_name)
    previous = state.last_online
    state.last_online = result.online
    if previous is None:
        if result.online:
            await send_auto_ping_notification(group_id, server_name, server_info, result)
    elif result.online != previous:
        await send_auto_ping_notification(group_id, server_name, server_info, result)


async def send_auto_ping_notification(
    group_id: int,
    server_name: str,
    server_info: ServerConfig,
    result: ServerProbeResult,
):
    if not var.handle_bot:
        return
    online = result.online
    status_text = "上线" if online else "下线"
    icon = "🟢" if online else "🔴"
    notification = MS.text(
        f"{icon} MC服务器【{server_name}】({server_info.host}) {status_text}\n"
    )
    notification += result.message
    try:
        await var.handle_bot.send_group_msg(
            group_id=group_id,
            message=notification,
        )
    except Exception:
        logger.exception("mc status notification failed to send")


AUTO_PING_MIN_INTERVAL = 30


@scheduler.scheduled_job(
    "interval",
    seconds=max(pc.mc_status_auto_ping_interval, AUTO_PING_MIN_INTERVAL),
)
async def auto_ping_job():
    if not var.data_loaded:
        return
    await run_auto_ping_cycle()
