"""
AI Transcriber 后端主服务
FunASR 本地转录 + B站/YouTube 链接转录 + AI纠错总结
"""
import os
import uuid
import subprocess
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import aiofiles
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp
from openai import OpenAI

# ==================== 配置 ====================
BASE_DIR = Path(__file__).parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
MODEL_DIR = BASE_DIR / "models"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

# 从环境变量读取配置
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
FUN_ASR_MODEL = os.environ.get("FUN_ASR_MODEL", "iic/SenseVoiceSmall")

# 设置 ffmpeg 路径（优先级：环境变量 > 剪映 > 系统）
FFMPEG_PATH = os.environ.get("FFMPEG_PATH")
if not FFMPEG_PATH:
    jianying_ffmpeg = Path("C:/Users/IT-DEV/AppData/Local/JianyingPro/Apps/10.6.0.14057/ffmpeg.exe")
    if jianying_ffmpeg.exists():
        FFMPEG_PATH = str(jianying_ffmpeg)
    else:
        FFMPEG_PATH = "ffmpeg"  # 使用系统PATH中的ffmpeg

print(f"[Config] ffmpeg路径: {FFMPEG_PATH}")

# 初始化 OpenAI 客户端（用于AI纠错和总结）
ai_client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY else None

# ASR 模型（延迟加载）
# 优先使用 Whisper，因为 FunASR 在 Windows 上有 PyTorch DLL 问题
asr_model = None
asr_backend = None  # "whisper" or "funasr"

def get_asr_model():
    """获取ASR模型（懒加载，优先Whisper）"""
    global asr_model, asr_backend
    if asr_model is None:
        # 尝试加载 Whisper
        try:
            import whisper
            print("[ASR] 加载 Whisper base 模型...")
            asr_model = whisper.load_model("base")
            asr_backend = "whisper"
            print("[ASR] Whisper 模型加载完成")
            return asr_model
        except Exception as e:
            print(f"[ASR] Whisper 加载失败: {e}")
        
        # 回退到 FunASR
        try:
            from funasr import AutoModel
            device = "cpu"  # FunASR 在 Windows 上可能有 DLL 问题
            print(f"[ASR] 尝试加载 FunASR: {FUN_ASR_MODEL}")
            asr_model = AutoModel(
                model=FUN_ASR_MODEL,
                device=device,
                disable_update=True
            )
            asr_backend = "funasr"
            print("[ASR] FunASR 模型加载完成")
        except Exception as e:
            print(f"[ASR] FunASR 加载失败: {e}")
            import traceback
            traceback.print_exc()
            asr_model = "error"
    return asr_model if asr_model != "error" else None


# ==================== 数据模型 ====================
class TranscriptionTask(BaseModel):
    id: str
    source: str  # file_url 或 video_url
    source_type: str  # "file" | "bilibili" | "youtube"
    status: str  # "pending" | "downloading" | "transcribing" | "correcting" | "completed" | "error"
    progress: int  # 0-100
    result_text: Optional[str] = None
    corrected_text: Optional[str] = None
    summary: Optional[str] = None
    error_msg: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


# 内存存储任务（简单版，生产环境用数据库）
tasks_db = {}


# ==================== FastAPI 应用 ====================
app = FastAPI(title="AI Transcriber", description="视频/音频转录服务")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
app.mount("/outputs", StaticFiles(directory=str(OUTPUT_DIR)), name="outputs")


# ==================== 工具函数 ====================
def download_video(url: str, task_id: str) -> str:
    """下载视频/音频，返回音频文件路径"""
    output_path = UPLOAD_DIR / task_id
    output_path.mkdir(exist_ok=True)
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(output_path / '%(id)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
        }],
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': os.path.dirname(FFMPEG_PATH) if FFMPEG_PATH != 'ffmpeg' else None,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        video_id = info.get('id', 'audio')
    
    # 找到下载的文件
    for f in output_path.iterdir():
        if f.suffix == '.wav':
            return str(f)
    
    # 如果没有wav，找其他音频格式
    for f in output_path.iterdir():
        if f.suffix in ['.mp3', '.m4a', '.aac', '.ogg']:
            return str(f)
    
    raise Exception("下载后未找到音频文件")


def transcribe_audio(audio_path: str) -> str:
    """使用ASR模型转录音频（优先云端API，回退本地）"""
    
    # 优先使用 OpenAI Whisper API（不依赖本地PyTorch）
    if OPENAI_API_KEY and OPENAI_BASE_URL:
        try:
            print(f"[ASR] 使用 OpenAI Whisper API 转录: {audio_path}")
            import openai
            client = openai.OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
            
            with open(audio_path, "rb") as f:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language="zh"
                )
            return transcript.text
        except Exception as e:
            print(f"[ASR] OpenAI API 失败: {e}，回退本地模型...")
    
    # 回退到本地模型
    model = get_asr_model()
    if model is None:
        raise Exception("ASR模型未加载，且未配置OpenAI API。请安装Visual C++运行时库修复PyTorch DLL问题。")
    
    # 根据后端类型选择推理方式
    if asr_backend == "whisper":
        # Whisper 推理
        print(f"[ASR] 使用本地 Whisper 转录: {audio_path}")
        result = model.transcribe(audio_path, language="zh")
        return result["text"]
    else:
        # FunASR 推理
        print(f"[ASR] 使用 FunASR 转录: {audio_path}")
        result = model.generate(input=audio_path)
        if isinstance(result, list) and len(result) > 0:
            text = result[0].get('text', '')
            text = re.sub(r'<\|[^|]+\|>','', text)
            return text
        return str(result)


async def ai_correct_and_summarize(text: str) -> dict:
    """使用AI纠错和生成摘要"""
    if ai_client is None:
        return {"corrected": text, "summary": "（未配置AI服务，跳过纠错和总结）"}
    
    try:
        # 纠错
        correct_prompt = f"""请对以下转录文本进行纠错，修正明显的错别字、标点符号和语句不通的地方。
保持原文意思不变，只修正错误。如果文本本身没有明显错误，直接返回原文。

原文：
{text}

修正后："""
        
        correct_resp = ai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": correct_prompt}],
            temperature=0.3,
        )
        corrected = correct_resp.choices[0].message.content.strip()
        
        # 生成摘要
        summary_prompt = f"""请对以下文本生成摘要，包括：
1. 主要内容概述（2-3句话）
2. 关键要点（3-5条）

文本：
{corrected}

摘要："""
        
        summary_resp = ai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.5,
        )
        summary = summary_resp.choices[0].message.content.strip()
        
        return {"corrected": corrected, "summary": summary}
    except Exception as e:
        print(f"[AI] 纠错/总结失败: {e}")
        return {"corrected": text, "summary": f"（AI处理失败: {str(e)}）"}


def process_task(task_id: str, source: str, source_type: str):
    """后台处理任务"""
    try:
        tasks_db[task_id]["status"] = "downloading"
        tasks_db[task_id]["progress"] = 10
        
        # 1. 获取音频文件
        if source_type == "file":
            audio_path = source  # source 是文件路径
        else:
            # 下载视频
            audio_path = download_video(source, task_id)
        
        tasks_db[task_id]["status"] = "transcribing"
        tasks_db[task_id]["progress"] = 30
        
        # 2. 转录
        text = transcribe_audio(audio_path)
        tasks_db[task_id]["result_text"] = text
        tasks_db[task_id]["status"] = "correcting"
        tasks_db[task_id]["progress"] = 70
        
        # 3. AI纠错和总结
        ai_result = ai_correct_and_summarize(text)
        tasks_db[task_id]["corrected_text"] = ai_result["corrected"]
        tasks_db[task_id]["summary"] = ai_result["summary"]
        
        # 4. 保存到文件
        output_file = OUTPUT_DIR / f"{task_id}.txt"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(f"# 转录结果\n\n")
            f.write(f"## 原始转录\n\n{text}\n\n")
            f.write(f"## AI纠错后\n\n{ai_result['corrected']}\n\n")
            f.write(f"## AI摘要\n\n{ai_result['summary']}\n\n")
        
        tasks_db[task_id]["status"] = "completed"
        tasks_db[task_id]["progress"] = 100
        tasks_db[task_id]["completed_at"] = datetime.now().isoformat()
        
    except Exception as e:
        tasks_db[task_id]["status"] = "error"
        tasks_db[task_id]["error_msg"] = str(e)
        print(f"[Task {task_id}] 错误: {e}")


# ==================== API 路由 ====================

@app.get("/")
def root():
    return {"service": "AI Transcriber", "version": "1.0.0", "status": "running"}


@app.post("/api/transcribe/upload")
async def upload_and_transcribe(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """上传音频/视频文件进行转录"""
    task_id = str(uuid.uuid4())
    
    # 保存上传的文件
    file_path = UPLOAD_DIR / task_id / file.filename
    file_path.parent.mkdir(exist_ok=True)
    
    async with aiofiles.open(file_path, 'wb') as f:
        content = await file.read()
        await f.write(content)
    
    # 创建任务
    tasks_db[task_id] = {
        "id": task_id,
        "source": str(file_path),
        "source_type": "file",
        "status": "pending",
        "progress": 0,
        "result_text": None,
        "corrected_text": None,
        "summary": None,
        "error_msg": None,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }
    
    # 后台处理
    background_tasks.add_task(process_task, task_id, str(file_path), "file")
    
    return {"task_id": task_id, "message": "任务已创建，正在处理中"}


@app.post("/api/transcribe/url")
async def transcribe_from_url(
    background_tasks: BackgroundTasks,
    url: str = Form(...),
):
    """从URL（B站/YouTube）转录"""
    # 判断URL类型
    if "bilibili.com" in url or "b23.tv" in url:
        source_type = "bilibili"
    elif "youtube.com" in url or "youtu.be" in url:
        source_type = "youtube"
    else:
        raise HTTPException(status_code=400, detail="不支持的URL，仅支持B站和YouTube")
    
    task_id = str(uuid.uuid4())
    
    # 创建任务
    tasks_db[task_id] = {
        "id": task_id,
        "source": url,
        "source_type": source_type,
        "status": "pending",
        "progress": 0,
        "result_text": None,
        "corrected_text": None,
        "summary": None,
        "error_msg": None,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }
    
    # 后台处理
    background_tasks.add_task(process_task, task_id, url, source_type)
    
    return {"task_id": task_id, "message": "任务已创建，正在处理中"}


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    """查询任务状态"""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail="任务不存在")
    return tasks_db[task_id]


@app.get("/api/task/{task_id}/result")
async def get_task_result(task_id: str):
    """获取任务结果文件"""
    output_file = OUTPUT_DIR / f"{task_id}.txt"
    if not output_file.exists():
        raise HTTPException(status_code=404, detail="结果文件不存在")
    return FileResponse(output_file, media_type="text/plain", filename=f"transcription_{task_id}.txt")


@app.get("/api/tasks")
async def list_tasks():
    """列出所有任务"""
    return {"tasks": list(tasks_db.values())}


@app.get("/api/health")
async def health_check():
    """健康检查"""
    asr_status = "loaded" if asr_model is not None else "not_loaded"
    ai_status = "configured" if ai_client is not None else "not_configured"
    return {
        "status": "healthy",
        "asr_model": asr_status,
        "ai_service": ai_status,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
