# AI Transcriber 🎤

智能转录助手 - 基于 FunASR 的视频/音频转录服务，支持 B站/YouTube 链接转录 + AI 纠错总结

## ✨ 功能特性

- 🎥 **多源支持**：支持本地文件上传、B站视频、YouTube 视频
- 🔤 **高精度转录**：基于 FunASR (SenseVoice) 的本地语音识别
- 🤖 **AI 增强**：自动纠错 + 智能摘要生成
- 🌐 **简洁界面**：Vue.js 构建的响应式 Web 界面
- 📊 **任务管理**：实时进度追踪、历史任务查看

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/HenrywooChem/ai-transcriber.git
cd ai-transcriber
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件，配置 API Key（用于AI纠错和总结）
```

### 3. 安装依赖

**后端：**
```bash
cd backend
pip install -r requirements.txt
```

**或使用 Docker：**
```bash
docker-compose up -d
```

### 4. 启动服务

**开发模式（Windows）：**
```powershell
.\start_dev.ps1
```

**手动启动：**
```bash
# 启动后端
cd backend
python main.py

# 启动前端（新终端）
cd frontend
python -m http.server 8080
```

### 5. 访问应用

- 前端界面：http://localhost:8080
- 后端 API：http://localhost:8001
- API 文档：http://localhost:8001/docs

## 📖 使用指南

### 方式一：链接转录

1. 打开首页，选择"链接转录"
2. 粘贴 B站/YouTube 视频链接
3. 点击"开始转录"
4. 等待处理完成（可实时查看进度）
5. 查看转录结果和 AI 摘要

### 方式二：文件上传

1. 选择"文件上传"
2. 选择本地音频/视频文件
3. 点击"开始转录"
4. 查看结果

## 🔧 配置说明

编辑 `.env` 文件：

```bash
# OpenAI 兼容 API（用于AI纠错和总结）
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# FunASR 模型（可换成其他模型）
FUN_ASR_MODEL=iic/SenseVoiceSmall

# 服务器配置
HOST=0.0.0.0
PORT=8001
```

**使用本地 Ollama：**
```bash
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=llama3.1
```

## 🏗️ 项目结构

```
ai-transcriber/
├── backend/              # 后端代码
│   ├── main.py          # FastAPI 主服务
│   ├── requirements.txt # Python 依赖
│   └── Dockerfile       # Docker 配置
├── frontend/            # 前端代码
│   └── index.html      # Vue.js 单页应用
├── uploads/             # 上传文件存储
├── outputs/             # 转录结果输出
├── models/              # ASR 模型缓存
├── docker-compose.yml   # Docker Compose 配置
├── start_dev.ps1       # 开发启动脚本
└── README.md           # 项目文档
```

## 📡 API 文档

### 上传文件转录

```bash
POST /api/transcribe/upload
Content-Type: multipart/form-data

file: [音频/视频文件]
```

### URL 转录

```bash
POST /api/transcribe/url
Content-Type: application/x-www-form-urlencoded

url=https://www.bilibili.com/video/BV...
```

### 查询任务状态

```bash
GET /api/task/{task_id}
```

### 获取任务列表

```bash
GET /api/tasks
```

## 🛠️ 技术栈

- **后端**：FastAPI + FunASR + yt-dlp
- **前端**：Vue.js 3
- **AI**：OpenAI API / Ollama（纠错和总结）
- **部署**：Docker + Docker Compose

## 📝 开发计划

- [ ] 支持更多视频平台（抖音、快手等）
- [ ] 批量转录
- [ ] 多语言支持
- [ ] 导出格式（SRT、VTT、TXT）
- [ ] 用户系统
- [ ] 说话人分离（Diarization）

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

MIT License

## 🙏 致谢

- [FunASR](https://github.com/alibaba-damo-academy/FunASR) - 阿里巴巴达摩院语音识别模型
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 视频下载工具
- [FastAPI](https://fastapi.tiangolo.com/) - 现代 Python Web 框架

---

**⭐ 如果这个项目对你有帮助，请给它一个 Star！**
