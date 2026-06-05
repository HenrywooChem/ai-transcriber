"""
视频转录服务 - 核心模块
支持 YouTube/B站 视频下载 → Whisper 转录 → LLM 后处理
"""
import os
import json
import time
import uuid
import asyncio
import shutil
from pathlib import Path
from typing import Optional

# 数据目录
DATA_DIR = Path("/home/ubuntu/video-transcribe/data")
DOWNLOADS_DIR = DATA_DIR / "downloads"
RESULTS_DIR = DATA_DIR / "results"
TASKS_FILE = DATA_DIR / "tasks.json"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# 加载任务记录
def load_tasks():
    if TASKS_FILE.exists():
        return json.loads(TASKS_FILE.read_text())
    return {}

def save_tasks(tasks):
    TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2))


class TranscriptionService:
    """转录服务 - 管理异步任务生命周期"""

    def __init__(self):
        self.tasks = load_tasks()
        self._whisper_model = None

    def get_whisper(self):
        if self._whisper_model is None:
            from faster_whisper import WhisperModel
            # CPU 优化配置
            self._whisper_model = WhisperModel(
                "base",  # base 模型 CPU 友好，也可以用 medium
                device="cpu",
                compute_type="int8",  # 8-bit 量化加速
                cpu_threads=4,
                num_workers=2
            )
        return self._whisper_model

    def create_task(self, url: str, platform: str = "auto",
                    use_llm: bool = True) -> str:
        """创建转录任务"""
        task_id = uuid.uuid4().hex[:12]
        self.tasks[task_id] = {
            "id": task_id,
            "url": url,
            "platform": platform,
            "use_llm": use_llm,
            "status": "queued",
            "progress": 0,
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": None
        }
        save_tasks(self.tasks)
        return task_id

    def get_task(self, task_id: str):
        return self.tasks.get(task_id)

    async def run_task(self, task_id: str):
        """异步执行转录任务"""
        task = self.tasks.get(task_id)
        if not task:
            return

        download_dir = DOWNLOADS_DIR / task_id
        download_dir.mkdir(exist_ok=True)

        try:
            # === 第1步：下载 ===
            await self._update_task(task_id, "downloading", 10, "正在下载视频...")
            audio_path = await self._download_audio(task["url"], download_dir)

            # === 第2步：转录 ===
            await self._update_task(task_id, "transcribing", 30, "正在语音转文字...")
            transcript = await self._transcribe(audio_path)

            # === 第3步：LLM 后处理 ===
            result = transcript
            if task.get("use_llm") and transcript.get("segments"):
                await self._update_task(task_id, "processing", 70, "正在用AI纠错和总结...")
                result = await self._llm_process(transcript)

            # === 保存结果 ===
            result_file = RESULTS_DIR / f"{task_id}.json"
            result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))

            task["result"] = result
            await self._update_task(task_id, "completed", 100, "完成")

        except Exception as e:
            task["error"] = str(e)
            await self._update_task(task_id, "failed", 0, f"失败: {e}")
        finally:
            # 清理下载文件
            if download_dir.exists():
                shutil.rmtree(download_dir, ignore_errors=True)

    async def _update_task(self, task_id, status, progress, message=""):
        task = self.tasks.get(task_id)
        if task:
            task["status"] = status
            task["progress"] = progress
            task["message"] = message
            task["updated_at"] = time.time()
            save_tasks(self.tasks)

    async def _download_audio(self, url: str, output_dir: Path) -> Path:
        """用 yt-dlp 下载音频"""
        output_template = str(output_dir / "%(title)s.%(ext)s")
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "-x", "--audio-format", "mp3",
            "--audio-quality", "0",
            "-o", output_template,
            "--no-playlist",
            "--print", "filename",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"下载失败: {stderr.decode()[:500]}")
        audio_path = stdout.decode().strip().split("\n")[-1]
        return Path(audio_path)

    async def _transcribe(self, audio_path: Path) -> dict:
        """用 faster-whisper 转录"""
        loop = asyncio.get_event_loop()
        model = self.get_whisper()

        def run_whisper():
            segments, info = model.transcribe(
                str(audio_path),
                language="zh",
                beam_size=3,
                vad_filter=True,  # 语音活动检测过滤静音
                vad_parameters=dict(min_silence_duration_ms=500)
            )
            result = {
                "language": info.language,
                "duration": round(info.duration, 1),
                "segments": []
            }
            for seg in segments:
                result["segments"].append({
                    "start": round(seg.start, 2),
                    "end": round(seg.end, 2),
                    "text": seg.text.strip()
                })
            return result

        return await loop.run_in_executor(None, run_whisper)

    async def _llm_process(self, transcript: dict) -> dict:
        """用 DashScope LLM 纠错和总结"""
        from openai import OpenAI

        # 拼接全文
        full_text = " ".join(s["text"] for s in transcript["segments"])

        client = OpenAI(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

        # 并行的纠错和总结
        corr_task = self._llm_correct(client, full_text)
        sum_task = self._llm_summarize(client, full_text)
        corrected_text, summary = await asyncio.gather(corr_task, sum_task)

        transcript["corrected_text"] = corrected_text
        transcript["summary"] = summary
        return transcript

    async def _llm_correct(self, client, text: str) -> str:
        """ASR 纠错"""
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model="qwen-turbo-latest",
            messages=[
                {"role": "system", "content": "你是一个语音转写文字校对专家。请纠正以下ASR转写文本中的错误（专有名词、同音字等），保持原意和口语风格不变。只返回纠正后的文本。"},
                {"role": "user", "content": text[:3000]}
            ],
            temperature=0.1,
            timeout=60
        )
        return resp.choices[0].message.content

    async def _llm_summarize(self, client, text: str) -> str:
        """内容总结"""
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model="qwen-turbo-latest",
            messages=[
                {"role": "system", "content": "请用中文总结以下视频/播客内容的要点，分点列出，简洁明了。"},
                {"role": "user", "content": text[:4000]}
            ],
            temperature=0.3,
            timeout=60
        )
        return resp.choices[0].message.content


# 全局服务实例
service = TranscriptionService()
