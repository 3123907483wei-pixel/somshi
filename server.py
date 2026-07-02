"""
MCP Server Starter — 入门级 MCP 服务器示例

提供基础的 Tools（工具）和 Resources（资源）供 LLM 客户端调用。
协议: Model Context Protocol (MCP) — Stdio 传输
"""

import os
import sys
import json
import logging
import platform
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones
from typing import Optional

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 日志配置：全部输出到 stderr，避免干扰 Stdio 通信
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp-server-starter")

# 加载 .env 文件（如果存在）
load_dotenv()

# 从环境变量读取配置
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "")

# ---------------------------------------------------------------------------
# 创建 MCP 服务器实例
# ---------------------------------------------------------------------------
server = FastMCP(
    "mcp-server-starter",
    description="一个适合新手入门的 MCP 服务器示例，提供时间查询、天气查询和文件读取功能。",
)

# ================================ T O O L S ================================


@server.tool()
async def get_local_time(timezone_name: str = "Asia/Shanghai") -> str:
    """获取指定时区的当前日期与时间。

    Args:
        timezone_name: IANA 时区名称（如 Asia/Shanghai、America/New_York、Europe/London），
                       默认为 Asia/Shanghai。
    """
    try:
        # 校验时区是否合法
        if timezone_name not in available_timezones():
            # 尝试模糊匹配
            matches = [tz for tz in available_timezones() if timezone_name.lower() in tz.lower()]
            if matches:
                return (
                    f"时区 '{timezone_name}' 不精确，您是否想要以下之一？\n"
                    + "\n".join(f"  - {m}" for m in matches[:10])
                )
            return f"错误：未知的时区 '{timezone_name}'。请使用 IANA 时区格式，例如 Asia/Shanghai。"

        tz = ZoneInfo(timezone_name)
        now = datetime.now(tz)
        weekday_map = {
            0: "星期一", 1: "星期二", 2: "星期三", 3: "星期四",
            4: "星期五", 5: "星期六", 6: "星期日",
        }

        return (
            f"🕐 当前时间（{timezone_name}）\n"
            f"  日期：{now.strftime('%Y-%m-%d')} {weekday_map[now.weekday()]}\n"
            f"  时间：{now.strftime('%H:%M:%S')}\n"
            f"  偏移：UTC{now.strftime('%z')}"
        )
    except Exception as e:
        logger.error("get_local_time 出错: %s", e)
        return f"错误：{str(e)}"


@server.tool()
async def get_weather(city: str) -> str:
    """查询指定城市的实时天气信息。

    如果配置了 WEATHER_API_KEY 则调用真实 API，否则返回模拟数据。

    Args:
        city: 城市名称（如 北京、上海、Tokyo、London）。
    """
    try:
        if WEATHER_API_KEY:
            return await _fetch_real_weather(city)
        else:
            return _mock_weather(city)
    except Exception as e:
        logger.error("get_weather 出错: %s", e)
        return f"查询天气时出错：{str(e)}"


@server.tool()
async def read_local_file(file_path: str) -> str:
    """读取本地指定路径的文件内容。

    安全限制：只能读取普通文本文件，且文件大小不超过 1 MB。

    Args:
        file_path: 文件的绝对路径（Windows 示例：C:\\Users\\Alice\\notes.txt）。
    """
    try:
        path = Path(file_path).resolve()

        # 安全校验
        if not path.exists():
            return f"错误：文件不存在 - {path}"

        if not path.is_file():
            return f"错误：路径不是文件 - {path}"

        # 检查文件大小（限制 1 MB）
        max_bytes = 1 * 1024 * 1024
        file_size = path.stat().st_size
        if file_size > max_bytes:
            return (
                f"错误：文件过大（{file_size / 1024 / 1024:.1f} MB），"
                f"超过 1 MB 的限制，无法读取。"
            )

        # 检测是否为文本文件（读取前 512 字节检查）
        try:
            with open(path, "rb") as f:
                head = f.read(512)
                head.decode("utf-8")
        except (UnicodeDecodeError, UnicodeError):
            return "错误：该文件不是可读的文本文件（二进制文件无法读取）。"

        # 读取文件内容
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        lines = content.split("\n")
        line_count = len(lines)

        header = f"📄 {path.name}（共 {line_count} 行，{file_size:,} 字节）\n"
        header += "─" * 50 + "\n"

        # 如果内容过大，截断显示
        max_lines = 500
        if line_count > max_lines:
            content = "\n".join(lines[:max_lines])
            content += f"\n\n...（仅显示前 {max_lines} 行，共 {line_count} 行）"
            header = f"📄 {path.name}（共 {line_count} 行，显示前 {max_lines} 行）\n"
            header += "─" * 50 + "\n"

        return header + content

    except PermissionError:
        return f"错误：没有权限读取该文件 - {file_path}"
    except Exception as e:
        logger.error("read_local_file 出错: %s", e)
        return f"读取文件时出错：{str(e)}"


# ============================  H E L P E R S  ==============================


def _mock_weather(city: str) -> str:
    """返回模拟天气数据（无 API Key 时的降级方案）。"""
    import random

    conditions = ["☀️ 晴", "⛅ 多云", "☁️ 阴", "🌦 小雨", "🌧 中雨", "⛈ 雷阵雨"]
    temp_base = {
        "北京": 28, "上海": 30, "广州": 32, "深圳": 31,
        "Tokyo": 26, "London": 18, "New York": 25, "Paris": 22,
    }

    condition = random.choice(conditions)
    base_temp = temp_base.get(city, 24)
    temp = base_temp + random.randint(-3, 3)
    humidity = random.randint(45, 85)
    wind = random.randint(1, 6)

    return (
        f"🌤 {city} 天气预报（模拟数据）\n"
        f"  天气状况：{condition}\n"
        f"  温    度：{temp}°C\n"
        f"  湿    度：{humidity}%\n"
        f"  风    速：{wind} 级\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 提示：配置 WEATHER_API_KEY 后可获取真实数据"
    )


async def _fetch_real_weather(city: str) -> str:
    """通过真实 API 查询天气。"""
    # 这里预留真实 API 集成接口
    # 例如使用 OpenWeatherMap API：
    # async with httpx.AsyncClient() as client:
    #     url = "https://api.openweathermap.org/data/2.5/weather"
    #     params = {"q": city, "appid": WEATHER_API_KEY, "lang": "zh_cn", "units": "metric"}
    #     resp = await client.get(url, params=params)
    #     data = resp.json()
    #     ...

    # 当前未集成真实 API，返回模拟数据
    logger.info("WEATHER_API_KEY 已配置，但真实 API 尚未集成，返回模拟数据")
    return _mock_weather(city)


# ============================= R E S O U R C E S ============================


@server.resource("file:///logs/app.log")
async def get_app_log() -> str:
    """暴露本地应用日志文件内容，供客户端按需读取。"""
    if not LOG_FILE_PATH:
        return "日志路径未配置（设置环境变量 LOG_FILE_PATH）"

    path = Path(LOG_FILE_PATH).resolve()
    if not path.exists():
        return f"日志文件不存在：{path}"

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        # 限制返回大小
        if len(content) > 100_000:
            content = content[-100_000:]
            content = f"...（仅显示最后 100 KB）\n{content}"
        return content
    except Exception as e:
        return f"读取日志文件失败：{str(e)}"


@server.resource("config://server/settings")
async def get_server_settings() -> str:
    """展示服务器当前运行的配置信息（JSON 格式）。"""
    settings = {
        "server": {
            "name": "mcp-server-starter",
            "version": "1.0.0",
            "protocol": "Model Context Protocol",
            "transport": "stdio",
        },
        "system": {
            "platform": platform.system(),
            "python_version": sys.version,
            "hostname": platform.node(),
        },
        "configuration": {
            "weather_api_configured": bool(WEATHER_API_KEY),
            "log_file_path": LOG_FILE_PATH or "未配置",
            "env_file_loaded": bool(os.getenv("WEATHER_API_KEY") is not None),
        },
        "tools": {
            "get_local_time": {"enabled": True, "description": "获取指定时区的当前时间"},
            "get_weather": {"enabled": True, "description": "查询指定城市的天气"},
            "read_local_file": {"enabled": True, "description": "读取本地文件内容"},
        },
    }
    return json.dumps(settings, ensure_ascii=False, indent=2)


# ================================ M A I N ==================================


def main():
    """启动 MCP 服务器（Stdio 模式）。"""
    logger.info("=" * 50)
    logger.info("MCP Server Starter 启动中...")
    logger.info(f"Python 版本: {sys.version}")
    logger.info(f"平台: {platform.system()} {platform.release()}")
    logger.info(f"天气 API: {'已配置' if WEATHER_API_KEY else '未配置（使用模拟数据）'}")
    logger.info(f"日志文件: {LOG_FILE_PATH or '未配置'}")
    logger.info("=" * 50)
    logger.info("已注册的工具 (Tools):")
    logger.info("  - get_local_time  : 获取指定时区的当前时间")
    logger.info("  - get_weather     : 查询指定城市的天气")
    logger.info("  - read_local_file : 读取本地文件内容")
    logger.info("已注册的资源 (Resources):")
    logger.info("  - file:///logs/app.log   : 应用日志")
    logger.info("  - config://server/settings : 服务器配置")
    logger.info("=" * 50)
    logger.info("MCP 服务器已就绪，等待客户端连接...")
    logger.info("不要直接查看此终端 —— 通过 Inspector 或 Claude Desktop 使用")
    logger.info("=" * 50)

    # 以 Stdio 模式运行
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
