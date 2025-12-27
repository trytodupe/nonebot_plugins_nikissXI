from dataclasses import dataclass
from json import dump, load
from os import makedirs, path
from typing import Dict, List, Optional, Union

from nonebot import get_bot, get_bots, get_driver, get_plugin_config
from nonebot.adapters import Bot
from pydantic import BaseModel


@dataclass
class ServerConfig:
    host: str
    server_type: str
    auto_ping: bool = False


@dataclass
class ServerState:
    last_online: Optional[bool] = None


class Config(BaseModel):
    # 管理员的QQ号（别问我为什么）
    mc_status_admin_qqnum: List[int] = []  # 必填
    # 机器人的QQ号（如果写了就按优先级响应，否则就第一个连上的响应） ['1234','5678','6666']
    mc_status_bot_qqnum_list: List[str] = []  # 可选
    # 数据文件名
    mc_status_data_filename: str = "mc_status_data.json"
    # 自动轮询的间隔（秒）
    mc_status_auto_ping_interval: int = 300


class Var:
    # 处理消息的bot
    handle_bot: Optional[Bot] = None
    #  {"123456": {"提肛": ServerConfig(...)}}
    group_list: Dict[int, Dict[str, ServerConfig]] = {}
    # 运行时的状态，方便自动通知
    server_states: Dict[int, Dict[str, ServerState]] = {}
    data_loaded: bool = False
    auto_ping_task = None


driver = get_driver()
pc = get_plugin_config(Config)
var = Var()


@driver.on_startup
async def on_startup():
    if not path.exists("data"):
        makedirs("data")

    if not path.exists(f"data/{pc.mc_status_data_filename}"):
        save_file()
    else:
        load_file()
    var.data_loaded = True


def load_file():
    var.group_list = {}
    var.server_states = {}
    with open(f"data/{pc.mc_status_data_filename}", "r", encoding="utf-8") as r:
        tmp_data = load(r)
        needs_save = False
        for group_key, servers in tmp_data.items():
            try:
                group_id = int(group_key)
            except (TypeError, ValueError):
                continue
            var.group_list[group_id] = {}
            var.server_states[group_id] = {}
            for name, raw in servers.items():
                host: Optional[str] = None
                server_type: Optional[str] = None
                auto_ping = False
                if isinstance(raw, list) and len(raw) >= 2:
                    host, server_type = raw[0], raw[1]
                    needs_save = True
                elif isinstance(raw, dict):
                    host = raw.get("host")
                    server_type = raw.get("type")
                    auto_ping = bool(raw.get("auto_ping", False))
                if not host or not server_type:
                    continue
                var.group_list[group_id][name] = ServerConfig(
                    host=host,
                    server_type=server_type,
                    auto_ping=auto_ping,
                )
                var.server_states[group_id][name] = ServerState()
        if needs_save:
            save_file()


def save_file():
    payload = {}
    for group_id, servers in var.group_list.items():
        payload[str(group_id)] = {}
        for name, info in servers.items():
            payload[str(group_id)][name] = {
                "host": info.host,
                "type": info.server_type,
                "auto_ping": info.auto_ping,
            }
    with open(f"data/{pc.mc_status_data_filename}", "w", encoding="utf-8") as w:
        dump(payload, w, indent=4, ensure_ascii=False)


def ensure_server_state(group_id: int, server_name: str) -> ServerState:
    if group_id not in var.server_states:
        var.server_states[group_id] = {}
    state = var.server_states[group_id].get(server_name)
    if state is None:
        state = ServerState()
        var.server_states[group_id][server_name] = state
    return state


# qq机器人连接时执行
@driver.on_bot_connect
async def on_bot_connect(bot: Bot):
    # 是否有写bot qq，如果写了只处理bot qq在列表里的
    if pc.mc_status_bot_qqnum_list and bot.self_id in pc.mc_status_bot_qqnum_list:
        # 如果已经有bot连了
        if var.handle_bot:
            # 当前bot qq 下标
            handle_bot_id_index = pc.mc_status_bot_qqnum_list.index(
                var.handle_bot.self_id
            )
            # 新连接的bot qq 下标
            new_bot_id_index = pc.mc_status_bot_qqnum_list.index(bot.self_id)
            # 判断优先级，下标越低优先级越高
            if new_bot_id_index < handle_bot_id_index:
                var.handle_bot = bot

        # 没bot连就直接给
        else:
            var.handle_bot = bot

    # 不写就给第一个连的
    elif not pc.mc_status_bot_qqnum_list and not var.handle_bot:
        var.handle_bot = bot


# qq机器人断开时执行
@driver.on_bot_disconnect
async def on_bot_disconnect(bot: Bot):
    # 判断掉线的是否为handle bot
    if bot == var.handle_bot:
        # 如果有写bot qq列表
        if pc.mc_status_bot_qqnum_list:
            # 获取当前连着的bot列表(需要bot是在bot qq列表里)
            available_bot_id_list = [
                bot_id for bot_id in get_bots() if bot_id in pc.mc_status_bot_qqnum_list
            ]
            if available_bot_id_list:
                # 打擂台排序？
                new_bot_index = pc.mc_status_bot_qqnum_list.index(
                    available_bot_id_list[0]
                )
                for bot_id in available_bot_id_list:
                    now_bot_index = pc.mc_status_bot_qqnum_list.index(bot_id)
                    if now_bot_index < new_bot_index:
                        new_bot_index = now_bot_index
                # 取下标在qq列表里最小的bot qq为新的handle bot
                var.handle_bot = get_bot(pc.mc_status_bot_qqnum_list[new_bot_index])

            else:
                var.handle_bot = None

        # 不写就随便给一个连着的(如果有)
        elif var.handle_bot:
            try:
                new_bot = get_bot()
                var.handle_bot = new_bot
            except ValueError:
                var.handle_bot = None
