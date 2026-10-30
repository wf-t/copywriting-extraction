# Copywriting Extraction

独立的视频语音文案提取工具。粘贴视频分享链接，解析视频信息，通过本地 Whisper 或阿里云 Fun-ASR 识别语音，再用 DeepSeek 整理完整文案、核心精华和可执行要点。

项目从 AIGC Content Studio 中拆分，可单独安装、运行和部署，不依赖原项目。包含用户提取页、浏览器提取记录页及 CDK 管理后台。

## 功能

- 支持抖音、小红书视频笔记、快手视频和 B 站公开视频。
- 支持完整分享文本、短链接；B 站多 P 视频按链接中的 `?p=N` 选择，默认第一 P。
- 支持本地 Whisper 与阿里云 Fun-ASR，后台可切换；两种引擎使用独立线程池，排队任务保持创建时的引擎。
- DeepSeek 整理语音转写，输出 Markdown 文案。
- CDK 支持 5、10、15、20 分钟四种视频时长档次，以及自定义总次数。
- 管理后台支持额度统计、组合查询、批量生成、TXT 导出、启停、按查询条件批量删除及短期文案记录查看。
- 用户浏览器保留 7 天本地提取记录，可复制、删除；服务器另存管理员可查的 7 天短期记录。

小红书和快手图片作品不做语音提取。平台解析依赖公开视频页面及临时媒体地址，平台规则、网络环境、视频访问权限和风控可能影响解析或云端拉取。

## 技术栈与目录

后端为 Python + FastAPI + SQLite，前端为 Vite + TypeScript + 原生 HTML/CSS。

```text
copy-studio/
├── backend/
│   ├── run.py                      # 独立服务入口
│   ├── requirements.txt            # 基础运行依赖
│   ├── requirements-dev.txt        # 测试依赖
│   ├── requirements-local.txt      # 可选本地 Whisper 依赖
│   ├── copy_studio/
│   │   ├── api.py                  # 提取、CDK、后台及页面路由
│   │   ├── commercial.py           # CDK、额度、会话与引擎设置
│   │   ├── extraction_history.py   # 服务器短期记录
│   │   ├── model_config.py         # DeepSeek / DashScope 配置
│   │   ├── model_runtime.py        # 文案整理所需的文本调用
│   │   └── transcript_extractor/
│   │       ├── service.py          # 异步提取任务与识别流程
│   │       └── platforms/         # 四个平台的独立适配器
│   └── tests/
├── frontend/
│   ├── transcript.html             # 用户提取页
│   ├── transcript-admin.html       # 管理后台
│   ├── transcript-records.html     # 本机记录页
│   └── src/
├── .github/workflows/ci.yml
├── .env.example
├── .gitignore
└── README.md
```

## 快速启动

建议使用 Python 3.11；前端要求 Node.js 22.12 或更高版本，以及 npm。以下命令在项目根目录执行，适用于 macOS / Linux。

### 1. 安装后端

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
cp .env.example .env
```

编辑 `.env`：设置 `CDK_ADMIN_USERNAME`、`CDK_ADMIN_PASSWORD` 和 `DEEPSEEK_API_KEY`。使用 Fun-ASR 时还需配置 `DASHSCOPE_API_KEY`。

`.env` 不会自动加载，每次启动服务前需要在当前终端执行：

```bash
set -a
source .env
set +a
```

### 2. 安装并构建前端

```bash
cd frontend
npm ci
npm run build
cd ..
```

### 3. 启动服务

```bash
python backend/run.py
```

构建后的三个页面由后端同源提供，不需要另外启动前端进程。

| 入口 | 地址 |
| --- | --- |
| 首页 | http://127.0.0.1:8765/ |
| 文案提取 | http://127.0.0.1:8765/copy-studio |
| 本机提取记录 | http://127.0.0.1:8765/copy-studio/records |
| CDK 管理后台 | http://127.0.0.1:8765/copy-studio/ft |
| 健康检查 | http://127.0.0.1:8765/api/health |
| API 文档 | http://127.0.0.1:8765/docs |

首次使用先登录管理后台，选择识别引擎并生成 CDK，再到用户页输入 CDK。后台初始识别引擎为 `local`；使用云端模式时，需在后台手动切换为阿里云 Fun-ASR。

未设置管理员环境变量时，本机调试默认账号为 `admin`，密码为 `ChangeMe-8765`；对外部署前必须修改。管理界面会提示仍在使用默认密码。

### 识别方式

**阿里云 Fun-ASR：** 配置 `DASHSCOPE_API_KEY`，在后台选择阿里云 Fun-ASR。服务直接提交平台适配器解析出的 HTTP/HTTPS 媒体地址，阿里云拉取媒体并识别；无需本机下载视频、安装 FFmpeg 或 Whisper，也不需要公网回调地址。临时媒体地址须可被云端访问。

**本地 Whisper：** 在运行服务的机器上安装 FFmpeg，并在虚拟环境中安装本地识别依赖：

```bash
python -m pip install -r backend/requirements-local.txt
ffmpeg -version
whisper --help
```

本地流程为“下载视频 → FFmpeg 提取音频 → Whisper turbo 识别”。首次识别会下载模型，模型缓存位于 `data/models/whisper/`。程序优先使用 `WHISPER_COMMAND` 指定的可执行文件，其次使用项目 `.venv/bin/whisper`，最后查找 `PATH`。

两种识别方式均需配置 DeepSeek 才能完成最终文案整理。

## 配置

| 环境变量 | 默认值 / 说明 |
| --- | --- |
| `CDK_ADMIN_USERNAME` | `admin`；部署时修改 |
| `CDK_ADMIN_PASSWORD` | `ChangeMe-8765`；部署时修改 |
| `DEEPSEEK_API_KEY` | 文案整理所需密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1`；兼容 Chat Completions 的服务地址 |
| `DEEPSEEK_MODEL` | `deepseek-chat` |
| `DASHSCOPE_API_KEY` | 阿里云 Fun-ASR 所需密钥 |
| `COPY_STUDIO_HOST` | `127.0.0.1` |
| `COPY_STUDIO_PORT` | `8765` |
| `COPY_STUDIO_ROOT` | 默认项目根目录；如覆盖，该目录同时包含 `data/` 与 `frontend/dist/` |
| `COOKIE_SECURE` | `false`；公网 HTTPS 部署设为 `true` |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1`；可信反向代理地址 |
| `TRANSCRIPT_LOCAL_WORKERS` | `1`；范围 1～4 |
| `TRANSCRIPT_FUN_ASR_WORKERS` | `5`；范围 1～32 |
| `WHISPER_COMMAND` | 可选，本地 Whisper 可执行文件路径或命令名 |

也可在本机创建 `data/model_config.json`，兼容原配置中的相关字段。环境变量优先；配置文件不提供前端编辑或公开读取接口。

```json
{
  "api_providers": {
    "deepseek": {
      "api_key": "YOUR_DEEPSEEK_API_KEY",
      "base_url": "https://api.deepseek.com/v1",
      "enable_proxy": false
    },
    "dashscope": {
      "api_key": "YOUR_DASHSCOPE_API_KEY"
    },
    "common": {
      "proxy": "",
      "print_model_input": false
    }
  },
  "models": { "llm": "deepseek-chat" }
}
```

## 前端开发

启动后端后，在另一个终端执行：

```bash
cd frontend
npm ci
npm run dev
```

访问 http://127.0.0.1:5173/ 。开发服务器支持相同的 `/copy-studio`、`/copy-studio/records`、`/copy-studio/ft` 路径，并将 `/api` 代理至后端，Cookie 保持同源。

后端地址变化时可使用 `COPY_STUDIO_API_TARGET=http://127.0.0.1:9000 npm run dev`。生产部署使用构建后的文件和后端服务。

## 数据与额度行为

- SQLite `data/commercial.db` 保存 CDK、额度、使用流水、用户会话与识别设置。
- “获取信息”不扣次数。“提取并整理文案”先解析视频真实时长，再通过 SQLite 事务校验时长档次、启用状态及剩余额度，成功扣 1 次后创建任务。超出时长限制、CDK 失效或额度耗尽时不扣次数；扣次后发生下载、识别或模型调用失败，当前实现不会自动退回次数。
- CDK 用完后仍可进入用户页查看本浏览器的记录，最后一次已创建任务仍可查询。
- 任务状态与中间结果仅存在进程内存中。服务重启后任务不恢复；任务访问按 CDK 校验所有权。
- 视频、音频和识别中间文件放在系统临时目录，任务结束时清理。Whisper 模型缓存会长期保留。
- 成功结果双写到浏览器 IndexedDB 与服务器 `data/cdk-history/<CDK ID>/`，只记录视频链接与最终文案。两端各保留 7 天，互不读取或同步。服务器记录仅供管理员查询，服务启动时及之后每 24 小时清理过期文件；浏览器记录在访问时清理。
- 更换浏览器、域名、端口或设备后，原浏览器记录不会自动迁移。

## Linux 部署

安装依赖、构建前端，配置并加载 `.env` 后启动 `python backend/run.py`。可使用 systemd 等进程管理方式运行，并通过 Nginx 反向代理启用 HTTPS。将 `COOKIE_SECURE=true`，按实际网络配置监听地址和可信代理地址。

当前任务队列和管理员会话保存在单进程内存中，部署时保持 **单个应用进程**，不要开启多个 Uvicorn worker 或多个应用副本；识别并发通过上述两个线程池参数调整。若用进程管理器直接启动 Uvicorn，可执行：

```bash
uvicorn copy_studio.api:app --app-dir backend --host 127.0.0.1 --port 8765 --workers 1
```

直接运行 Uvicorn 时，监听地址和端口由命令行参数指定。`COPY_STUDIO_HOST`、`COPY_STUDIO_PORT` 由 `backend/run.py` 读取。

## 测试与构建

在项目根目录执行：

```bash
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
PYTHONPATH=backend python -m unittest discover -s backend/tests -v
cd frontend
npm ci
npm run typecheck
npm run build
```

测试覆盖平台解析、识别任务隔离、引擎切换、CDK 并发扣次、短期记录和独立服务接口。测试模拟外部平台与模型服务，不需要真实密钥，也不会发起付费识别或模型调用。GitHub Actions 会自动运行后端测试与前端构建。

## 上传到 GitHub

GitHub 仓库为 [`wf-t/copywriting-extraction`](https://github.com/wf-t/copywriting-extraction)，新目录首次上传时在本项目根目录执行。若目录已初始化 Git，可跳过 `git init`。

```bash
git init -b main
git add .
git status
git commit -m "Initial standalone Copywriting Extraction"
git remote add origin https://github.com/wf-t/copywriting-extraction.git
git push -u origin main
```

`.gitignore` 排除 `.env`、`commercial.env`、`data/`、虚拟环境、模型、数据库、提取记录、`node_modules/`、构建产物、日志与常见密钥文件。仓库保留前端依赖锁文件，克隆后自行安装依赖和配置密钥。

本次拆分仅迁移源码和测试，不迁移原项目的 Git 历史、密钥、CDK 数据库、用户记录或模型文件。若需要沿用旧 CDK，可停服后备份并迁移完整 SQLite 数据库；本地配置仅需迁移 DeepSeek / DashScope 相关字段。原浏览器 IndexedDB 记录仍受原站点来源限制。
