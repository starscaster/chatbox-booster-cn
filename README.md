# 🚀 Chatbox Booster

> 增强基础 AI Chatbox 功能，赋予其联网搜索、网页抓取、PDF 读取、人机交互等能力。

**作者:** [@starscater](https://github.com/starscaster)　|　**语言:** Python 3.10+（推荐 3.12）

---

## 📖 目录

- [简介](#-简介)
- [功能特性](#-功能特性)
- [安装](#-安装)
- [配置 MCP 客户端](#-配置-mcp-客户端)
- [工具一览](#-工具一览)
- [配置](#️-配置)
- [使用示例](#-使用示例)
- [项目结构](#-项目结构)
- [未来计划](#-未来计划)
- [许可](#-许可)

---

## 📌 简介

**Chatbox Booster** 是一个 [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) 工具集，专为 AI Chatbox / LLM 客户端设计。它提供一组即插即用的工具，让对话式 AI 突破文本交互的局限，能够：

- 🌐 **实时联网搜索**，获取最新信息
- 📄 **抓取网页正文**，提取结构化文本
- 📚 **检索学术论文**（arXiv），并直接解析 PDF
- 🖱️ **与用户进行 GUI 交互**——确认、输入、填表
- 🕒 **获取系统时间**，校准时效性

---

## ✨ 功能特性

| 类别 | 能力 |
|------|------|
| 🌍 网络搜索 | 全网搜索，支持地区/语言过滤、AI 内容评估 |
| 📝 网页抓取 | 自动提取网页正文，去除 HTML 标签与噪音 |
| 🎓 学术检索 | arXiv 论文检索 + PDF 直链解析 |
| 💬 用户交互 | GUI 确认对话框、单行输入、结构化多题问卷 |
| ⏰ 系统工具 | 获取当前系统时间 |

---

## 🔧 安装

### 环境要求

- **Python:** ≥ 3.10（推荐 3.12）— [下载 Python](https://www.python.org/downloads/)
- **操作系统:** Windows / macOS / Linux

> 💡 **没有安装过 Python？** 前往 [python.org](https://www.python.org/downloads/) 下载安装包，安装时务必勾选 **「Add Python to PATH」**（Windows）。安装完成后在终端输入 `python --version` 验证。

### 步骤

```bash
# 1. 克隆仓库
git clone https://github.com/starscaster/chatbox-booster-cn.git
cd chatbox-booster

# 2. 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# 2.5  在虚拟环境安装 pip 24.0 版本（Python ≥ 3.12 建议执行，可选）
python -m pip install pip==24.0 --no-cache-dir

# 3. 安装依赖
pip install -r requirements.txt

# 4. 验证安装
python server.py --help
```

---

## 🖥️ 配置 MCP 客户端

安装完成后，需要在你的 MCP 客户端（如 Chatbox、Claude Desktop 等）中配置此工具集。配置方式是在客户端的 MCP 配置中添加一个 JSON 条目。

### 获取脚本路径

```bash
# Windows (在项目目录中执行)
cd /d "你的项目路径"
echo %cd%\server.py

# macOS / Linux (在项目目录中执行)
pwd
# 将输出的路径后面加上 /server.py
```

> 💡 **使用虚拟环境时**，需要指定虚拟环境中的 Python 解释器路径，而非系统 Python：
> - **Windows:** `.venv\Scripts\python.exe`
> - **macOS / Linux:** `.venv/bin/python`

### 配置 JSON（直接使用，仅需填入路径）

以下 JSON 覆盖了本项目的所有工具，**直接复制**到你的 MCP 客户端配置中，将 `<项目路径>` 替换为实际的绝对路径即可：

#### Windows

```json
{
  "mcpServers": {
    "chatbox-booster": {
      "command": "<项目路径>\\.venv\\Scripts\\python.exe",
      "args": [
        "<项目路径>\\server.py"
      ],
      "env": {}
    }
  }
}
```

#### macOS / Linux

```json
{
  "mcpServers": {
    "chatbox-booster": {
      "command": "<项目路径>/.venv/bin/python",
      "args": [
        "<项目路径>/server.py"
      ],
      "env": {}
    }
  }
}
```

### Chatbox 配置指引

1. 打开 Chatbox，进入 **设置 → MCP 服务器**
2. 点击 **添加 MCP 服务器**
3. 将上方对应系统的 JSON 粘贴到配置框中
4. 将 `<项目路径>` 替换为实际的绝对路径（例如 `C:/Users/你的用户名/chatbox-booster` 或 `/home/用户/chatbox-booster`）
5. 保存配置，Chatbox 将自动启动 MCP 服务
6. 在对话中即可使用联网搜索、PDF 阅读等工具

> ⚠️ **注意**：路径分隔符请使用 `/` 或 `\\`，避免使用单个 `\`（Chatbox 中会被转义）。

### Claude Desktop 配置指引

编辑 Claude Desktop 的配置文件 `claude_desktop_config.json`，将上面的 JSON 合并到其中的 `mcpServers` 字段即可。

---

## 🧰 工具一览

### 1. `DDGS_web_search` — DuckDuckGo 网络搜索

覆盖面广，智能排序。支持地区过滤与 AI 内容质量评估。

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | 搜索关键词 |
| `max_results` | int | 最大返回数（默认 5） |
| `region` | string | 地域-语言代码，如 `zh-cn`（默认 `wt-wt` 不限） |
| `ai_evaluate` | bool | 是否启用 LLM 内容筛选（默认 false） |
| `intent` | string | AI 评估侧重点描述 |

### 2. `fetch_webpage_tool` — 网页抓取

打开 HTTP/HTTPS 网页并提取纯文本正文。

| 参数 | 类型 | 说明 |
|------|------|------|
| `url` | string | 网页 URL（需含协议头） |
| `timeout` | int | 超时秒数（默认 15） |
| `max_chars` | int | 返回最大字符数（默认 8000） |

### 3. `arxiv_search` — 学术论文搜索

搜索 arXiv 预印本论文。

| 参数 | 类型 | 说明 |
|------|------|------|
| `query` | string | 搜索关键词 |
| `max_results` | int | 最大返回数（默认 5） |

### 4. `pdf_reader` — PDF 解析

从 URL 下载并解析 PDF，提取文本内容。适用于 arXiv PDF 链接或其他公开 PDF 文档。

| 参数 | 类型 | 说明 |
|------|------|------|
| `pdf_url` | string | PDF 文件 URL |
| `max_pages` | int | 最大读取页数（默认 5） |
| `timeout` | int | 下载超时秒数（默认 30） |

### 5. `get_date` — 获取系统时间

校准系统时间，避免 AI 提供过时信息。

### 6. `interactive_dialog_UA` — 确认对话框

弹出 Yes / No / Cancel 三按钮对话框，用于敏感操作确认。

| 参数 | 类型 | 说明 |
|------|------|------|
| `title` | string | 窗口标题 |
| `message` | string | 提示内容 |
| `timeout` | int | 超时秒数（默认 120） |
| `default` | string | 默认聚焦按钮（默认 "否"） |

### 7. `interactive_dialog_input` — 单行输入对话框

弹出单行文本输入框，向用户征集简短信息。

| 参数 | 类型 | 说明 |
|------|------|------|
| `title` | string | 窗口标题 |
| `message` | string | 提示内容 |
| `timeout` | int | 超时秒数（默认 120） |
| `default` | string | 预填文本（默认空） |

### 8. `interactive_dialog_inquiry` — 结构化问卷

自动解析问题描述，生成多题/多选问卷表单。

| 参数 | 类型 | 说明 |
|------|------|------|
| `title` | string | 窗口标题 |
| `message` | string | 提示说明 |
| `inquires` | string | 问题描述，支持自动拆分 |
| `inquiry_type` | string | `single_question` / `multiple_question` / `multiple_options` |
| `other` | string | 每题追加"其他"输入框（`enable`/`disable`） |
| `remarks` | string | 末尾追加备注输入框（`enable`/`disable`） |
| `timeout` | int | 超时秒数（默认 300） |

### 9. `Serper_web_search` — Google 搜索

通过 Serper API 进行 Google 搜索，作为 DuckDuckGo 的补充搜索引擎。

### 10. `add` — 两数相加 🔬

示例/测试工具，用于验证 MCP 工具链路正常。

---

## ⚙️ 配置

项目通过 `config.json` 管理 API 密钥、代理和评估模型等设置。首次使用前请编辑该文件。

### 🔑 建议配置

以下两项**推荐所有用户配置**：

#### DeepSeek API Key

`ai_eval` 依赖 DeepSeek API 进行搜索内容智能评估。请填入你的 API Key（[获取](https://platform.deepseek.com/)）：

```json
{
  "api": {
    "ai_eval": {
      "api_key": "你的DeepSeek密钥"
    }
  }
}
```

#### DuckDuckGo 代理（中国大陆用户）

国内网络无法直连 DuckDuckGo。请将代理设为你的 HTTP 代理地址：

```json
{
  "proxy": {
    "ddgs": "http://127.0.0.1:你的代理端口"
  }
}
```

> 📎 代理软件通常提供本地 HTTP 端口，如 Clash 默认 `7890`，V2Ray 默认 `10808`或`10809`。

### 🛠 可选配置

以下配置项建议使用默认值，可按需修改：

| 配置路径 | 说明 | 默认值 |
|---------|------|--------|
| `proxy.ddgs` | DuckDuckGo 搜索代理 | `http://127.0.0.1:10808` |
| `proxy.arxiv` | arXiv 论文检索代理 | `null`（直连） |
| `api.rerank.url` | Rerank 模型服务地址 | `api.siliconflow.cn/v1/rerank` |
| `api.rerank.api_key` | Rerank 服务 API Key | 需自行填写 |
| `api.rerank.model` | Rerank 模型名称 | `BAAI/bge-reranker-v2-m3` |
| `api.ai_eval.url` | AI 评估 API 地址（默认使用deepseek-v4-flash） | `https://api.deepseek.com/chat/completions` |
| `api.ai_eval.api_key` | AI 评估 API Key | 需自行填写 |
| `api.ai_eval.model` | AI 评估模型 | `deepseek-v4-flash` |
| `api.serper.url` | Serper 搜索 API 地址 | `https://google.serper.dev/search` |
| `api.serper.api_key` | Serper API Key（可选） | 留空则使用 DDGS |

### 📦 完整示例

```json
{
  "proxy": {
    "ddgs": "http://127.0.0.1:10808",
    "arxiv": null
  },
  "api": {
    "rerank": {
      "url": "api.siliconflow.cn/v1/rerank",
      "api_key": "你的硅基流动 api key",
      "model": "BAAI/bge-reranker-v2-m3",
      "timeout": 10.0,
      "max_tokens": 6144
    },
    "ai_eval": {
      "url": "https://api.deepseek.com/chat/completions",
      "api_key": "你的DeepSeek api key",
      "model": "deepseek-v4-flash",
      "timeout": 20.0,
      "max_tokens": 64000,
      "retry_count": 1
    },
    "serper": {
      "url": "https://google.serper.dev/search",
      "api_key": "你的Serper api key"
    }
  }
}
```
