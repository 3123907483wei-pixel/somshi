# MCP Server Starter

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP Spec](https://img.shields.io/badge/MCP-2025--03--26-purple.svg)](https://spec.modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个轻量级、适合新手入门的 **MCP（Model Context Protocol）服务器** 示例项目。  
本项目旨在帮助开发者快速理解 MCP 规范的核心概念，并实践如何构建可供大语言模型客户端（如 Claude Desktop）调用的 **Tools（工具）** 与 **Resources（资源）**。

---

## 目录

- [MCP 通信原理](#mcp-通信原理)
- [功能特性](#功能特性)
- [项目结构](#项目结构)
- [前置条件](#前置条件)
- [快速开始](#快速开始)
- [客户端配置集成](#客户端配置集成)
- [开发调试](#开发调试)
- [开发与扩展](#开发与扩展)
- [许可证](#许可证)

---

## MCP 通信原理

> ⚠️ **重要：理解这一点才能正确使用 MCP 服务器。**

MCP 服务器**不**是一个可以被直接 `python server.py` 启动并看到输出画面的 Web 服务。它的核心通信机制是 **Stdio（Standard Input / Output，标准输入输出）**：

```
┌──────────────────────┐          JSON-RPC 消息         ┌──────────────────────┐
│   LLM 客户端          │  ────── (通过 stdin/stdout) ──→  │  MCP 服务器          │
│  (Claude Desktop 等)  │  ←───────────────────────────  │  (你的 server.py)     │
└──────────────────────┘                                 └──────────────────────┘
```

- 客户端通过 **标准输入（stdin）** 向服务器发送 JSON-RPC 格式的请求
- 服务器通过 **标准输出（stdout）** 返回 JSON-RPC 格式的响应
- **标准错误（stderr）** 保留给日志输出，方便开发者调试
- 直接在终端运行 `python server.py` 不会有任何可见输出——因为程序只是在等待 stdin 输入，你看到的只是"挂起"状态，**这不是正确用法**

> 💡 **正确用法**：MCP 服务器必须由 MCP 客户端（Claude Desktop、VS Code 等）**作为子进程启动**，通过 Stdio 管道进行通信。详见下方的[客户端配置集成](#客户端配置集成)与[开发调试](#开发调试)章节。

---

## 功能特性

本 MCP 服务器目前提供以下能力：

### 🛠️ Tools（工具）

| 工具名称 | 描述 | 输入参数 |
|---------|------|---------|
| `get_local_time` | 获取指定时区的当前日期与时间 | `timezone`（可选，默认 `Asia/Shanghai`） |
| `get_weather` | 查询指定城市的实时天气信息 | `city`（必填，城市名称） |
| `read_local_file` | 读取本地指定路径的文件内容 | `file_path`（必填，文件绝对路径） |

### 📂 Resources（资源）

| 资源 URI | 描述 |
|----------|------|
| `file:///logs/app.log` | 暴露本地应用日志文件内容，供客户端按需读取 |
| `config://server/settings` | 展示服务器当前运行的配置信息（JSON 格式） |

> 💡 **提示：** 以上均为示例功能，你可以在此基础上自由扩展。

---

## 项目结构

```
mcp-server-starter/
├── server.py              # MCP 服务器入口，注册 Tools 和 Resources
├── requirements.txt       # Python 依赖清单
├── .env.example           # 环境变量模板
├── README.md              # 本文件
└── LICENSE                # MIT 许可证
```

---

## 前置条件

在开始之前，请确保你的开发环境中已安装以下工具：

- **Python 3.10+** — 可在终端用 `python --version` 确认版本
- **pip** — Python 包管理器（通常随 Python 一同安装）
- **uv（强烈推荐）** — 快速的 Python 包管理工具，安装方式：

  ```powershell
  # PowerShell（Windows）
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  > 如果不想安装 uv，也可以使用标准 pip，但后续的 `uvx` 配置方式将不可用。

- **Node.js 18+（调试用）** — 用于运行 MCP Inspector 调试工具，从 [nodejs.org](https://nodejs.org/) 下载安装

---

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/yourusername/mcp-server-starter.git
cd mcp-server-starter
```

### 2. 创建虚拟环境并安装依赖

```powershell
# 创建虚拟环境
python -m venv .venv

# 激活虚拟环境（Windows）
.venv\Scripts\activate

# macOS / Linux:
# source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 3. 配置环境变量（可选）

将 `.env.example` 复制为 `.env`，并按需填入配置：

```powershell
copy .env.example .env
```

查看 `.env.example` 内容：

```
# .env.example
WEATHER_API_KEY=your_api_key_here
LOG_FILE_PATH=/var/log/myapp/app.log
```

> 如果不需要天气 API，可以跳过此步骤，`get_weather` 将返回模拟数据。

### ✅ 如何验证服务器能正常工作？

安装完成后，**不要直接运行 `python server.py`**。请使用以下任意一种方式验证：

#### 方式 A：使用 MCP Inspector（浏览器调试界面，推荐）

```powershell
npx @modelcontextprotocol/inspector python server.py
```

这将启动一个本地 Web 调试界面（通常访问 `http://localhost:5173`），你可以在浏览器中直观地测试所有 Tools 和 Resources。

#### 方式 B：编写简易测试脚本

创建一个 `test_server.py`：

```python
import subprocess
import json

# 启动 MCP 服务器子进程
proc = subprocess.Popen(
    ["python", "server.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

# 发送初始化请求
request = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {}
})

stdout, stderr = proc.communicate(input=request + "\n")
print("服务器响应:", stdout)
```

---

## 客户端配置集成

要将此 MCP 服务器接入大模型客户端（如 Claude Desktop），请编辑客户端的配置文件。

### Claude Desktop 配置

编辑 `claude_desktop_config.json`：

- **Windows 路径：** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS 路径：** `~/.config/Claude/claude_desktop_config.json`

#### 方式一：通过 UVX 运行（推荐）

> ⚠️ 前提：已安装 [uv](https://docs.astral.sh/uv/)。uvx 会自动创建隔离环境，无需手动管理 venv。

```json
{
  "mcpServers": {
    "mcp-server-starter": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/yourusername/mcp-server-starter",
        "python",
        "server.py"
      ]
    }
  }
}
```

如果你的代码在**本地目录**：

```json
{
  "mcpServers": {
    "mcp-server-starter": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:/Users/你的用户名/projects/mcp-server-starter",
        "server.py"
      ]
    }
  }
}
```

#### 方式二：指定虚拟环境的 Python 解释器（最稳妥）

```json
{
  "mcpServers": {
    "mcp-server-starter": {
      "command": "C:\\Users\\你的用户名\\projects\\mcp-server-starter\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\你的用户名\\projects\\mcp-server-starter\\server.py"
      ]
    }
  }
}
```

> ⚠️ **Windows 路径注意事项：**
> - JSON 字符串中的反斜杠必须转义，请使用双反斜杠 `\\` 或正斜杠 `/`
> - ✅ 正确：`"C:\\Users\\Alice\\project\\server.py"`
> - ✅ 正确：`"C:/Users/Alice/project/server.py"`
> - ❌ 错误：`"C:\Users\Alice\project\server.py"`

### 验证集成

配置完成后，**重启 Claude Desktop**。在对话界面中你应该能够看到 Tools 图标出现，尝试提问：

- _"现在北京时间是多少？"_
- _"上海今天天气怎么样？"_
- _"帮我读一下本地的日志文件"_

如果 Claude Desktop 右侧没有出现工具图标，请检查：
1. `claude_desktop_config.json` 的 JSON 格式是否正确（可用 [JSONLint](https://jsonlint.com/) 校验）
2. 路径中的 `python.exe` 是否存在
3. 查看 Claude Desktop 日志（`%APPDATA%\Claude\logs\`）排查错误

---

## 开发调试

### 使用 MCP Inspector（官方调试工具）

Anthropic 官方提供了 `@modelcontextprotocol/inspector`，它在浏览器中提供了一个图形化界面，让你可以无需客户端即可调试 MCP 服务器：

```powershell
npx @modelcontextprotocol/inspector python server.py
```

启动后：

1. 终端会显示 `WebSocket server started at ws://localhost:5173` 等日志
2. 浏览器自动打开 `http://localhost:5173`
3. 在 Inspector 界面中，你可以：
   - **列出所有已注册的 Tools 和 Resources**
   - **手动调用任意 Tool** 并实时查看返回结果
   - **读取任意 Resource** 的内容
   - **查看 JSON-RPC 原始请求与响应**，便于深入理解协议细节

### 日志调试技巧

在 `server.py` 中，所有通过 `print(..., file=sys.stderr)` 或 `logging` 模块输出的内容都会被发送到 **stderr**，不会干扰 Stdio 通信：

```python
import sys

# 调试日志输出到 stderr（安全，不影响 MCP 协议通信）
print("get_local_time 被调用了", file=sys.stderr, flush=True)
```

这些日志在 Inspector 的控制台或客户端的日志文件中可见。

---

## 开发与扩展

### 添加一个新的 Tool

在 `server.py` 中，使用 `@server.tool()` 装饰器注册一个新函数。以下是两个实用示例：

#### 示例 1：数学计算工具

```python
from mcp.server import Server

server = Server("mcp-server-starter")

@server.tool()
async def calculate(expression: str) -> str:
    """计算数学表达式并返回结果"""
    try:
        # 注意：生产环境应使用 safer 的解析方式
        result = eval(expression, {"__builtins__": {}}, {})
        return f"计算结果：{result}"
    except Exception as e:
        return f"计算错误：{str(e)}"
```

#### 示例 2：读取工作目录文件列表（Windows 友好）

这个工具展示了如何处理 Windows 路径编码问题：

```python
import os
import sys
from pathlib import Path

@server.tool()
async def list_workspace_files(directory: str = ".") -> str:
    """
    列出指定目录下的所有文件及其基本信息。
    适用于 Windows / macOS / Linux 跨平台场景。
    """
    try:
        # 使用 pathlib 处理 Windows 路径编码
        target = Path(directory).resolve()

        if not target.exists():
            return f"错误：路径不存在 - {target}"

        if not target.is_dir():
            return f"错误：路径不是目录 - {target}"

        files = []
        for entry in target.iterdir():
            info = {
                "name": entry.name,
                "type": "目录" if entry.is_dir() else "文件",
                "size": entry.stat().st_size if entry.is_file() else 0,
            }
            files.append(info)

        # 按类型排序：目录在前，文件在后
        files.sort(key=lambda x: (x["type"], x["name"]))

        lines = [f"📁 {target} 中的内容：", "─" * 40]
        for f in files:
            icon = "📁" if f["type"] == "目录" else "📄"
            size_str = f" ({f['size']} bytes)" if f["size"] > 0 else ""
            lines.append(f"  {icon} {f['name']}{size_str}")
        lines.append(f"─" * 40)
        lines.append(f"共 {len(files)} 项")

        return "\n".join(lines)
    except Exception as e:
        return f"读取目录时出错：{str(e)}"
```

#### 核心要点

1. 使用 `@server.tool()` 装饰器将函数注册为 Tool
2. 函数签名中的**类型注解**（`str`, `int`, `bool` 等）会自动映射为工具的输入参数 schema
3. 函数的 **docstring** 会成为该工具的描述信息，供大模型理解使用场景
4. 函数应为 `async` 异步函数以支持并发调用
5. Windows 环境下务必使用 `pathlib.Path` 处理路径，避免编码问题

### 添加一个新的 Resource

```python
@server.resource("config://server/version")
async def get_server_version() -> str:
    """返回当前服务器版本信息"""
    return json.dumps({"version": "1.0.0", "build": "2026-07-01"})
```

---

## 许可证

本项目基于 **MIT 许可证** 开源 — 详见 [LICENSE](LICENSE) 文件。

```
MIT License

Copyright (c) 2026 yourname

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files...
```

---

<p align="center">
  用 ❤️ 构建 · 仅供学习 MCP 规范之用
</p>