"""
视频转录服务 - Web应用版
支持：文件上传转录 + URL下载转录 + API Key鉴权
"""
import os, json, time, uuid, asyncio, shutil
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# ============================================================
# 加载环境变量（兼容 .env 文件）
# ============================================================
_env_file = Path("/home/ubuntu/.hermes/.env")
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k not in os.environ:  # 不覆盖已有的环境变量
                os.environ[k] = v.strip('"').strip("'")

# 确保关键 Key 存在
_DASHSCOPE_KEY = os.environ.get("DASHSCOPE_API_KEY", "")

# ============================================================
# 配置
# ============================================================
DATA_DIR = Path("/home/ubuntu/video-transcribe/data")
UPLOAD_DIR = DATA_DIR / "uploads"
RESULTS_DIR = DATA_DIR / "results"
TASKS_FILE = DATA_DIR / "tasks.json"
STATIC_DIR = Path(__file__).parent / "static"
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
CLEANUP_HOURS = 24                  # 24小时后清理

for d in [UPLOAD_DIR, RESULTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="视频转录服务", version="1.0.0")

# 挂载静态文件（CSS/JS）
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ============================================================
# API Key 鉴权（简单模式）
# ============================================================
API_KEYS_FILE = DATA_DIR / "api_keys.json"
API_KEYS = {}

def load_api_keys():
    global API_KEYS
    if API_KEYS_FILE.exists():
        API_KEYS = json.loads(API_KEYS_FILE.read_text())
    else:
        # 默认 Key
        API_KEYS = {
            "sk-demo": {"name": "demo", "created_at": time.time(), "used": 0}
        }
        save_api_keys()

def save_api_keys():
    API_KEYS_FILE.write_text(json.dumps(API_KEYS, ensure_ascii=False, indent=2))

load_api_keys()

def verify_key(request: Request):
    """从请求头或查询参数获取 API Key"""
    key = request.headers.get("X-API-Key") or request.query_params.get("key")
    if key and key in API_KEYS:
        API_KEYS[key]["used"] += 1
        save_api_keys()
        return API_KEYS[key]["name"]
    return None

# ============================================================
# 任务管理
# ============================================================
tasks = {}

def load_tasks():
    global tasks
    if TASKS_FILE.exists():
        tasks = json.loads(TASKS_FILE.read_text())
    else:
        tasks = {}

def save_tasks():
    TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2))

load_tasks()

# ============================================================
# FunASR 模型（替换 faster-whisper）
# ============================================================
_funasr_model = None

def get_funasr():
    global _funasr_model
    if _funasr_model is None:
        from funasr import AutoModel
        _funasr_model = AutoModel(
            model="iic/SenseVoiceSmall",   # 识别+情感，中英日韩粤
            device="cpu",
            disable_update=True,            # 不检查更新，加速启动
        )
    return _funasr_model

# ============================================================
# 核心功能
# ============================================================
async def transcribe_audio(audio_path: Path) -> dict:
    """用 FunASR 进行语音转录
    将音频切成 30 秒小块，逐块转录，避免大音频 OOM，同时获得时间戳
    """
    loop = asyncio.get_event_loop()
    model = get_funasr()

    def _get_duration() -> float:
        """用 ffprobe 获取音频时长（秒）"""
        import subprocess, json
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", str(audio_path)
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        info = json.loads(r.stdout)
        for s in info.get("streams", []):
            dur = s.get("duration")
            if dur:
                return float(dur)
        return 0.0

    def _split_audio(duration: float) -> list:
        """用 ffmpeg 将音频切成 30 秒一块，返回 (chunk_path, start_sec, end_sec) 列表"""
        import subprocess, tempfile
        chunks = []
        chunk_dur = 25  # 25 秒一块，留安全余量
        start = 0.0

        tmpdir = Path(audio_path).parent
        while start < duration:
            end = min(start + chunk_dur, duration)
            chunk_path = tmpdir / f"chunk_{int(start)}_{int(end)}.wav"
            cmd = [
                "ffmpeg", "-y", "-ss", str(start), "-t", str(end - start),
                "-i", str(audio_path),
                "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                str(chunk_path)
            ]
            subprocess.run(cmd, capture_output=True, timeout=60)
            if chunk_path.exists() and chunk_path.stat().st_size > 100:
                chunks.append((chunk_path, start, end))
            start = end
        return chunks

    def _run():
        duration = _get_duration()
        import re

        # 验证音频可用性
        if duration <= 0:
            return {
                "language": "zh",
                "duration": 0,
                "segments": [{"start": 0.0, "end": 0.0, "text": "(音频时长获取失败)"}]
            }

        # 限制最大处理时长（超过 30 分钟不予处理，防 OOM）
        max_duration = 1800  # 30 分钟
        if duration > max_duration:
            duration = max_duration

        # 音频很短（< 30秒），直接转录
        if duration <= 30:
            try:
                result = model.generate(input=str(audio_path))
                full_text = ""
                for item in result:
                    text = item.get("text", "")
                    clean = re.sub(r'<\|[^|]+\|>', '', text).strip()
                    if clean:
                        full_text = (full_text + " " + clean).strip()
                text = full_text or "(无识别结果)"
            except Exception as e:
                return {
                    "language": "zh",
                    "duration": round(duration, 2),
                    "segments": [{"start": 0.0, "end": round(duration, 2), "text": f"(转录失败: {str(e)[:100]})"}]
                }
            return {
                "language": "zh",
                "duration": round(duration, 2),
                "segments": [{"start": 0.0, "end": round(duration, 2), "text": text}]
            }

        # 音频较长，切块转录（每块 25 秒，避免 OOM）
        chunks = _split_audio(duration)
        if not chunks:
            return {"language": "zh", "duration": round(duration, 2), "segments": [
                {"start": 0.0, "end": round(duration, 2), "text": "(音频切块失败)"}
            ]}

        all_segments = []
        full_text = ""
        for chunk_path, start, end in chunks:
            try:
                result = model.generate(input=str(chunk_path))
                text = ""
                for item in result:
                    t = item.get("text", "")
                    clean = re.sub(r'<\|[^|]+\|>', '', t).strip()
                    if clean:
                        text = (text + " " + clean).strip()
                if text:
                    all_segments.append({
                        "start": round(start, 2),
                        "end": round(end, 2),
                        "text": text
                    })
                    full_text = (full_text + " " + text).strip()
            except Exception as e:
                all_segments.append({
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "text": f"(段转录失败: {str(e)[:80]})"
                })
            finally:
                if chunk_path.exists():
                    chunk_path.unlink()

        if not all_segments:
            all_segments = [{"start": 0.0, "end": round(duration, 2), "text": "(无识别结果)"}]
            full_text = "(无识别结果)"

        return {
            "language": "zh",
            "duration": round(duration, 2),
            "segments": all_segments
        }

    return await loop.run_in_executor(None, _run)

async def llm_process(transcript: dict) -> dict:
    if not _DASHSCOPE_KEY:
        transcript["llm_error"] = "未配置 DASHSCOPE_API_KEY，无法使用 AI 纠错和总结"
        return transcript

    full_text = " ".join(s["text"] for s in transcript["segments"])
    from openai import OpenAI
    client = OpenAI(
        api_key=_DASHSCOPE_KEY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    async def correct():
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model="qwen-turbo",
            messages=[
                {"role": "system", "content": "你是一个语音转写文字校对专家。请纠正以下ASR转写文本中的错误（专有名词、同音字等），保持原意和口语风格不变。只返回纠正后的文本。"},
                {"role": "user", "content": full_text[:3000]}
            ],
            temperature=0.1, timeout=60)
        return resp.choices[0].message.content
    async def summarize():
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model="qwen-turbo",
            messages=[
                {"role": "system", "content": "请用中文总结以下视频/播客内容的要点，分点列出，简洁明了。"},
                {"role": "user", "content": full_text[:4000]}
            ],
            temperature=0.3, timeout=60)
        return resp.choices[0].message.content
    transcript["corrected_text"], transcript["summary"] = await asyncio.gather(correct(), summarize())
    return transcript

async def download_audio(url: str, output_dir: Path) -> Path:
    """通用视频音频下载
    策略（层层降级）：
    1. B站 → Camofox 专用提取（已实现，不走 yt-dlp）
    2. 其他网站 → yt-dlp 试一次（YouTube/Twitter 等海外站无需 cookie）
    3. yt-dlp 失败 → Camofox 通用页面提取（抖音/快手/小红书等国内站）
    """
    url_lower = url.lower()

    # B站走专用策略（Camofox 提取音视频流）
    if "bilibili.com" in url_lower or "b23.tv" in url_lower:
        return await download_bilibili_via_camofox(url, output_dir)

    # 非B站：先试 yt-dlp（免费、无 cookie 要求可下 YouTube/Twitter/微博等）
    try:
        return await download_youtube_via_ytdlp(url, output_dir)
    except Exception:
        pass  # yt-dlp 失败，走 Camofox 通用提取

    # Camofox 通用提取（真实浏览器打开页面，提取 video 源）
    try:
        return await download_via_camofox_generic(url, output_dir)
    except Exception as e:
        raise HTTPException(400, f"无法下载此视频（已尝试 yt-dlp 和浏览器提取均失败）: {str(e)[:200]}")


async def download_bilibili_via_camofox(url: str, output_dir: Path) -> Path:
    """通过 Camofox 浏览器下载 B站音频"""
    import aiohttp

    camofox_base = "http://localhost:9377"
    user_id = "transcriber"
    tab_id = None

    async def _camofox_post(path: str, data: dict = None) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{camofox_base}{path}",
                                    json=data or {},
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Camofox API {path} 返回 {resp.status}: {text[:200]}")
                return await resp.json()

    async def _camofox_get(path: str) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{camofox_base}{path}",
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Camofox API {path} 返回 {resp.status}: {text[:200]}")
                return await resp.json()

    try:
        # 1. 创建标签页（先打开B站首页建立 session）
        tab_data = await _camofox_post("/tabs", {
            "userId": user_id,
            "sessionKey": f"bili_{uuid.uuid4().hex[:8]}",
            "url": "https://www.bilibili.com/"
        })
        tab_id = tab_data.get("tabId")
        if not tab_id:
            raise RuntimeError("Camofox 创建标签页失败")

        # 2. 等首页加载
        await asyncio.sleep(3)

        # 3. 导航到视频页
        await _camofox_post(f"/tabs/{tab_id}/navigate", {
            "userId": user_id,
            "url": url
        })

        # 4. 等待视频页面完全加载
        await asyncio.sleep(5)

        # 4. 执行 JS 提取播放信息
        js_code = """
        (function() {
            try {
                var scripts = document.querySelectorAll('script');
                for (var i = 0; i < scripts.length; i++) {
                    var t = scripts[i].textContent || '';
                    if (t.indexOf('__playinfo__') > -1) {
                        var start = t.indexOf('{');
                        var end = t.lastIndexOf('}');
                        if (start > -1 && end > start) {
                            var json = JSON.parse(t.substring(start, end + 1));
                            var audio = json && json.data && json.data.dash && json.data.dash.audio;
                            if (audio && audio.length > 0) {
                                var best = audio[0];
                                return JSON.stringify({
                                    audioUrl: best.baseUrl || '',
                                    backupUrl: (best.backupUrl && best.backupUrl[0]) || '',
                                    duration: json.data.dash.duration || 0,
                                    title: document.title || ''
                                });
                            }
                        }
                    }
                }
                return 'no_playinfo';
            } catch(e) {
                return 'error:' + e.message;
            }
        })()
        """

        exec_result = await _camofox_post(f"/tabs/{tab_id}/evaluate", {
            "userId": user_id,
            "expression": js_code
        })

        result_text = exec_result.get("result", "")

        # 第一次不成功则尝试直接提取页面 HTML 中的 playinfo
        if result_text in ("no_playinfo", "") or result_text.startswith("error:"):
            await asyncio.sleep(3)
            exec_result = await _camofox_post(f"/tabs/{tab_id}/evaluate", {
                "userId": user_id,
                "expression": js_code
            })
            result_text = exec_result.get("result", "")

        if not result_text or result_text.startswith("error:") or result_text == "no_playinfo":
            raise RuntimeError(f"无法从B站页面提取音频信息: {result_text[:200]}")

        playinfo = json.loads(result_text)
        audio_url = playinfo.get("audioUrl") or playinfo.get("backupUrl")
        if not audio_url:
            raise RuntimeError("未找到音频流地址")

        title = playinfo.get("title", "bilibili_video")[:50]
        # 清理文件名中的非法字符
        import re
        safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)
        output_path = output_dir / f"{safe_title}.m4a"

        # 6. 用 curl 下载音频流（带浏览器请求头）
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s", "-L", "-o", str(output_path),
            "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "-H", "Referer: https://www.bilibili.com/",
            "--connect-timeout", "15",
            "--max-time", "120",
            audio_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            # 尝试备用 URL
            backup_url = playinfo.get("backupUrl")
            if backup_url and backup_url != audio_url:
                proc = await asyncio.create_subprocess_exec(
                    "curl", "-s", "-L", "-o", str(output_path),
                    "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                    "-H", "Referer: https://www.bilibili.com/",
                    "--connect-timeout", "15",
                    "--max-time", "120",
                    backup_url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                _, stderr = await proc.communicate()

            if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
                err_msg = stderr.decode()[:200] if stderr else "下载文件为空"
                raise RuntimeError(f"音频流下载失败: {err_msg}")

        file_size = output_path.stat().st_size
        if file_size < 1000:
            raise RuntimeError(f"音频文件过小 ({file_size} 字节)，可能下载不完整")

        # 转成 mp3（统一格式）
        mp3_path = output_dir / f"{safe_title}.mp3"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(output_path),
            "-vn", "-acodec", "libmp3lame", "-ab", "128k",
            str(mp3_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

        # 清理临时 m4a
        if output_path.exists():
            output_path.unlink()

        if mp3_path.exists() and mp3_path.stat().st_size > 1000:
            return mp3_path
        raise RuntimeError("音频转码失败")

    finally:
        # 7. 清理标签页
        if tab_id:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.delete(f"{camofox_base}/tabs/{tab_id}?userId={user_id}",
                                         timeout=aiohttp.ClientTimeout(total=5))
            except Exception:
                pass


# ============================================================
# Camofox 通用视频提取（支持抖音/快手/小红书/微博等）
# ============================================================
async def download_via_camofox_generic(url: str, output_dir: Path) -> Path:
    """通用方案：用 Camofox 浏览器打开任意视频页面，提取视频源下载"""
    import aiohttp
    import re as _re

    camofox_base = "http://localhost:9377"
    user_id = "transcriber"
    tab_id = None

    async def _camofox_post(path: str, data: dict = None) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{camofox_base}{path}",
                                    json=data or {},
                                    timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Camofox API {path} 返回 {resp.status}: {text[:200]}")
                return await resp.json()

    async def _camofox_get(path: str) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{camofox_base}{path}",
                                   timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Camofox API {path} 返回 {resp.status}: {text[:200]}")
                return await resp.json()

    try:
        # 1. 创建标签页（先打开目标网站首页建立 session）
        domain = url.split("/")[2] if "://" in url else url.split("/")[0]
        tab_data = await _camofox_post("/tabs", {
            "userId": user_id,
            "sessionKey": f"gen_{uuid.uuid4().hex[:8]}",
            "url": f"https://{domain}/"
        })
        tab_id = tab_data.get("tabId")
        if not tab_id:
            raise RuntimeError("Camofox 创建标签页失败")

        await asyncio.sleep(2)

        # 2. 导航到目标视频页
        await _camofox_post(f"/tabs/{tab_id}/navigate", {
            "userId": user_id,
            "url": url
        })

        # 3. 等待页面加载（不同网站加载速度不同）
        await asyncio.sleep(5)

        # 4. 执行 JS 提取视频信息（多种策略）
        js_extract = r"""
        (function() {
            var result = {};

            // 策略1: 找 <video> 标签
            var videos = document.querySelectorAll('video');
            if (videos.length > 0) {
                result.videoCount = videos.length;
                result.videoSrc = videos[0].src || '';
                // 检查 <source> 子元素
                var sources = videos[0].querySelectorAll('source');
                if (sources.length > 0) {
                    result.sourceSrc = sources[0].src;
                }
                // 检查 currentSrc（浏览器已解析的最终地址）
                result.currentSrc = videos[0].currentSrc || '';
                // 检查 poster（封面，间接证明视频存在）
                result.poster = videos[0].poster || '';
            }

            // 策略2: 取页面标题
            result.title = document.title || '';

            // 策略3: 在页面文本中搜索视频文件 URL
            var html = (document.documentElement && document.documentElement.outerHTML) || '';
            // 匹配 mp4 / m3u8 / webm
            var foundUrls = [];
            var patterns = [
                /https?:\/\/[^\s\"'<>]+?\.(mp4|m3u8|webm|ts)(\?[^\s\"'<>]*)?/gi,
                /https?:\/\/[^\s\"'<>]+?video[^\s\"'<>]*?\.(mp4|m3u8)/gi,
                /https?:\/\/[^\s\"'<>]+?\/(play|videoplayback)[^\s\"'<>]*/gi
            ];
            for (var pi = 0; pi < patterns.length; pi++) {
                var matches = html.match(patterns[pi]);
                if (matches) {
                    for (var mi = 0; mi < Math.min(matches.length, 5); mi++) {
                        foundUrls.push(matches[mi]);
                    }
                }
            }
            if (foundUrls.length > 0) {
                result.foundVideoUrls = foundUrls;
            }

            return JSON.stringify(result);
        })()
        """

        exec_result = await _camofox_post(f"/tabs/{tab_id}/evaluate", {
            "userId": user_id,
            "expression": js_extract
        })

        result_text = exec_result.get("result", "")
        if not result_text or result_text == "undefined":
            raise RuntimeError("无法从页面提取视频信息（页面可能未加载完成）")

        import json as _json
        page_info = _json.loads(result_text)
        title = page_info.get("title", "video")[:50]

        # 确定最佳视频源
        video_url = (
            page_info.get("currentSrc") or
            page_info.get("videoSrc") or
            page_info.get("sourceSrc") or
            (page_info.get("foundVideoUrls") and page_info["foundVideoUrls"][0])
        )

        if not video_url:
            raise RuntimeError(
                f"未在页面中找到可下载的视频源。\n"
                f"页面标题: {page_info.get('title', '未知')}\n"
                f"视频元素: {page_info.get('videoCount', 0)} 个\n"
                f"{'（此网站可能需要其他处理方式）' if page_info.get('videoCount', 0) == 0 else '（视频元素未加载或受保护）'}"
            )

        # 安全文件名
        safe_title = _re.sub(r'[\\/*?:"<>|]', "_", title) or "video"
        safe_title = safe_title[:80]

        # 5. 用 curl 下载
        output_path = output_dir / f"{safe_title}.mp4"

        # 判断是音频还是视频（音频链接不转码，视频需提取音频）
        is_audio = any(video_url.lower().endswith(ext) for ext in ['.m4a', '.mp3', '.aac', '.wav', '.ogg'])

        proc = await asyncio.create_subprocess_exec(
            "curl", "-s", "-L", "-o", str(output_path),
            "-H", f"Referer: https://{domain}/",
            "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "--connect-timeout", "15",
            "--max-time", "180",
            video_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size < 1000:
            err_msg = stderr.decode()[:200] if stderr else "文件过小"
            # 清理可能存在的空文件
            if output_path.exists():
                output_path.unlink()
            raise RuntimeError(f"视频流下载失败: {err_msg}")

        file_size = output_path.stat().st_size

        if is_audio:
            # 已经是音频，确保 mp3 格式
            mp3_path = output_dir / f"{safe_title}.mp3"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(output_path),
                "-vn", "-acodec", "libmp3lame", "-ab", "128k",
                str(mp3_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            if output_path.exists():
                output_path.unlink()
            if mp3_path.exists() and mp3_path.stat().st_size > 1000:
                return mp3_path
            raise RuntimeError("音频转码失败")
        else:
            # 视频文件，提取音频轨道
            mp3_path = output_dir / f"{safe_title}.mp3"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(output_path),
                "-vn", "-acodec", "libmp3lame", "-ab", "128k",
                str(mp3_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            # 清理原始视频文件
            if output_path.exists():
                output_path.unlink()
            if mp3_path.exists() and mp3_path.stat().st_size > 1000:
                return mp3_path
            raise RuntimeError("视频转音频失败")

    finally:
        if tab_id:
            try:
                async with aiohttp.ClientSession() as session:
                    await session.delete(f"{camofox_base}/tabs/{tab_id}?userId={user_id}",
                                         timeout=aiohttp.ClientTimeout(total=5))
            except Exception:
                pass


async def download_youtube_via_ytdlp(url: str, output_dir: Path) -> Path:
    """用 yt-dlp 下载 YouTube 音频"""
    output_template = str(output_dir / "%(title)s.%(ext)s")
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "-o", output_template, "--no-playlist", "--print", "filename",
        url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_msg = stderr.decode()[:500]
        raise HTTPException(400, f"下载失败: {err_msg}")
    return Path(stdout.decode().strip().split("\n")[-1])

def segments_to_srt(segments: list) -> str:
    def _fmt(sec):
        h, m, s = int(sec // 3600), int((sec % 3600) // 60), sec % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(f"{i}")
        lines.append(f"{_fmt(seg['start'])} --> {_fmt(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)

# ============================================================
# 后台任务
# ============================================================
bg_tasks_set = set()

async def run_file_task(task_id: str, audio_path: Path, use_llm: bool):
    try:
        tasks[task_id].update({"status": "transcribing", "progress": 30, "message": "🎙️ 正在语音转文字..."})
        save_tasks()
        transcript = await transcribe_audio(audio_path)
        result_file = RESULTS_DIR / f"{task_id}.json"
        result_file.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

        if use_llm:
            tasks[task_id].update({"status": "processing", "progress": 70, "message": "🤖 正在AI纠错和总结..."})
            save_tasks()
            try:
                transcript = await llm_process(transcript)
                result_file.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
            except Exception as llm_err:
                transcript["llm_error"] = str(llm_err)
                result_file.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
                tasks[task_id].update({"message": f"转录完成，AI处理失败: {str(llm_err)[:100]}"})

        tasks[task_id].update({"status": "completed", "progress": 100, "message": "✅ 完成", "result": transcript})
    except Exception as e:
        tasks[task_id].update({"status": "failed", "message": str(e), "error": str(e)})
    finally:
        save_tasks()
        bg_tasks_set.discard(task_id)
        if audio_path.exists():
            audio_path.unlink()

async def run_url_task(task_id: str, url: str, use_llm: bool):
    download_dir = UPLOAD_DIR / task_id
    download_dir.mkdir(exist_ok=True)
    try:
        tasks[task_id].update({"status": "downloading", "progress": 15, "message": "📥 正在下载视频..."})
        save_tasks()
        audio_path = await download_audio(url, download_dir)

        tasks[task_id].update({"status": "transcribing", "progress": 40, "message": "🎙️ 正在语音转文字..."})
        save_tasks()
        transcript = await transcribe_audio(audio_path)

        result_file = RESULTS_DIR / f"{task_id}.json"
        result_file.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))

        if use_llm:
            tasks[task_id].update({"status": "processing", "progress": 75, "message": "🤖 正在AI纠错和总结..."})
            save_tasks()
            try:
                transcript = await llm_process(transcript)
                result_file.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
            except Exception as llm_err:
                transcript["llm_error"] = str(llm_err)
                result_file.write_text(json.dumps(transcript, ensure_ascii=False, indent=2))
                tasks[task_id].update({"message": f"转录完成，AI处理失败: {str(llm_err)[:100]}"})

        # 提取视频标题
        title = audio_path.stem if audio_path else url
        tasks[task_id].update({"status": "completed", "progress": 100, "message": "✅ 完成", "result": transcript, "title": title})
    except Exception as e:
        tasks[task_id].update({"status": "failed", "message": str(e), "error": str(e)})
    finally:
        save_tasks()
        bg_tasks_set.discard(task_id)
        if download_dir.exists():
            shutil.rmtree(download_dir, ignore_errors=True)

# ============================================================
# API 路由
# ============================================================

@app.get("/")
async def root():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>视频转录服务</h1><p>前端页面未就绪，请等待部署完成。</p>")

@app.get("/api/status")
async def status():
    return {
        "service": "视频转录服务", "version": "1.0.0",
        "tasks_total": len(tasks),
        "tasks_pending": sum(1 for t in tasks.values() if t["status"] not in ("completed", "failed"))
    }

@app.post("/api/transcribe/upload")
async def transcribe_upload(
    request: Request,
    file: UploadFile = File(...),
    use_llm: bool = Form(True)
):
    """上传文件转录"""
    key_name = verify_key(request)
    if key_name is None and len(API_KEYS) > 1:
        raise HTTPException(401, "需要有效的 API Key (通过 X-API-Key 请求头或 ?key= 参数)")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, f"文件过大，最大支持 {MAX_FILE_SIZE // 1024 // 1024}MB")

    task_id = uuid.uuid4().hex[:12]
    ext = Path(file.filename).suffix or ".mp3"
    save_path = UPLOAD_DIR / f"{task_id}{ext}"
    save_path.write_bytes(content)

    tasks[task_id] = {
        "id": task_id, "url": f"upload://{file.filename}",
        "status": "queued", "progress": 0, "message": "⏳ 排队中",
        "created_at": time.time(), "result": None, "error": None,
        "mode": "upload", "use_llm": use_llm, "user": key_name or "anonymous"
    }
    save_tasks()

    t = asyncio.create_task(run_file_task(task_id, save_path, use_llm))
    bg_tasks_set.add(task_id)
    return {"task_id": task_id, "status": "queued", "filename": file.filename, "file_size": len(content)}

@app.post("/api/transcribe/url")
async def transcribe_url(
    request: Request,
    data: dict
):
    """通过URL下载并转录（支持YouTube/B站）"""
    key_name = verify_key(request)
    if key_name is None and len(API_KEYS) > 1:
        raise HTTPException(401, "需要有效的 API Key")

    url = data.get("url", "").strip()
    # 从包含标题的粘贴文本中提取实际 URL
    import re as _re
    url_match = _re.search(r'https?://[^\s]+', url)
    if url_match:
        url = url_match.group(0)
    use_llm = data.get("use_llm", True)
    if not url:
        raise HTTPException(400, "请提供视频URL")

    # 解析平台（扩展支持更多网站）
    platform = "other"
    url_lower = url.lower()
    if "bilibili.com" in url_lower or "b23.tv" in url_lower:
        platform = "bilibili"
    elif "youtube.com" in url_lower or "youtu.be" in url_lower:
        platform = "youtube"
    elif "douyin.com" in url_lower or "iesdouyin.com" in url_lower:
        platform = "douyin"
    elif "kuaishou.com" in url_lower or "gifshow.com" in url_lower:
        platform = "kuaishou"
    elif "xiaohongshu.com" in url_lower or "xhslink.com" in url_lower:
        platform = "xiaohongshu"
    elif "weibo.com" in url_lower or "weibo.cn" in url_lower:
        platform = "weibo"
    elif "x.com" in url_lower or "twitter.com" in url_lower:
        platform = "twitter"

    task_id = uuid.uuid4().hex[:12]
    tasks[task_id] = {
        "id": task_id, "url": url, "platform": platform,
        "status": "queued", "progress": 0, "message": "⏳ 排队中",
        "created_at": time.time(), "result": None, "error": None,
        "mode": "url", "use_llm": use_llm, "user": key_name or "anonymous"
    }
    save_tasks()

    t = asyncio.create_task(run_url_task(task_id, url, use_llm))
    bg_tasks_set.add(task_id)
    return {"task_id": task_id, "status": "queued", "url": url, "platform": platform}

@app.get("/api/task/{task_id}")
async def get_task(task_id: str):
    t = tasks.get(task_id)
    if not t:
        result_file = RESULTS_DIR / f"{task_id}.json"
        if result_file.exists():
            # 从文件恢复
            return {"task_id": task_id, "status": "completed", "message": "✅ 完成", "progress": 100}
        raise HTTPException(404, "任务不存在")
    resp = {
        "task_id": t.get("id", task_id), "status": t["status"],
        "progress": t["progress"], "message": t.get("message", ""),
        "error": t.get("error"),
        "mode": t.get("mode", "upload"),
        "title": t.get("title"),
        "result_url": f"/view/{task_id}" if t["status"] == "completed" else None
    }
    resp["result"] = t.get("result")
    return resp

@app.get("/api/tasks")
async def list_tasks(limit: int = 30):
    sorted_tasks = sorted(tasks.values(), key=lambda t: t.get("created_at", 0), reverse=True)[:limit]
    return [{
        "task_id": t.get("id", "?"), "status": t["status"],
        "url": t.get("url", "?"), "progress": t["progress"],
        "message": t.get("message", ""),
        "created_at": t.get("created_at", 0),
        "mode": t.get("mode", "upload"),
        "title": t.get("title")
    } for t in sorted_tasks]

@app.get("/api/export/{task_id}")
async def export_task(task_id: str, fmt: str = "txt"):
    rfile = RESULTS_DIR / f"{task_id}.json"
    if not rfile.exists():
        raise HTTPException(404, "结果不存在或未完成")
    result = json.loads(rfile.read_text())

    if fmt == "json":
        return JSONResponse(result)
    elif fmt == "srt":
        srt_content = segments_to_srt(result.get("segments", []))
        content_type = "text/plain; charset=utf-8"
    else:  # txt
        lines = []
        for s in result.get("segments", []):
            m, s_sec = divmod(int(s["start"]), 60)
            lines.append(f"[{m:02d}:{s_sec:02d}] {s['text']}")
        if result.get("corrected_text"):
            lines.append(f"\n{'='*40}\nAI 纠错\n{'='*40}\n{result['corrected_text']}")
        if result.get("summary"):
            lines.append(f"\n{'='*40}\n内容总结\n{'='*40}\n{result['summary']}")
        srt_content = "\n".join(lines)

    return HTMLResponse(
        f"<pre style='font-family:monospace;white-space:pre-wrap;max-width:900px;margin:20px auto;background:#f8f8f8;padding:20px;border-radius:8px;'>{srt_content}</pre>",
        headers={"Content-Type": "text/plain; charset=utf-8"}
    )

# ============================================================
# 结果查看页面（美化版）
# ============================================================
@app.get("/view/{task_id}")
async def view_result(task_id: str):
    t = tasks.get(task_id)
    result = None
    rfile = RESULTS_DIR / f"{task_id}.json"
    if rfile.exists():
        result = json.loads(rfile.read_text())

    if not t and not result:
        return HTMLResponse("<h1>任务不存在</h1>", status_code=404)

    status = t["status"] if t else "completed"

    if status in ("queued", "downloading", "transcribing", "processing"):
        msg_map = {
            "queued": "⏳ 排队中",
            "downloading": "📥 正在下载...",
            "transcribing": "🎙️ 正在转录...",
            "processing": "🤖 AI 处理中..."
        }
        body = f"""
        <div class="status-card">
            <h2>{msg_map.get(status, status)}</h2>
            <div class="progress-bar"><div class="progress-fill" style="width:{t.get('progress', 0)}%"></div></div>
            <p class="status-msg">{t.get('message', '')}</p>
        </div>
        <script>setTimeout(()=>location.reload(),3000)</script>
        """
    elif status == "failed":
        body = f"""
        <div class="status-card error">
            <h2>❌ 转录失败</h2>
            <p class="error-msg">{t.get('error', '未知错误')}</p>
        </div>
        """
    elif status == "completed" and result:
        segs = result.get("segments", [])
        seg_html = ""
        for s in segs[:100]:
            m, sec = divmod(int(s["start"]), 60)
            seg_html += f'<div class="segment"><span class="ts">{m:02d}:{sec:02d}</span><span class="txt">{s["text"]}</span></div>'
        if len(segs) > 100:
            seg_html += f'<p class="more">… 共 {len(segs)} 段，查看完整版请下载 TXT/SRT</p>'

        lang = result.get("language", "zh")
        duration = result.get("duration", 0)
        dm, ds = divmod(int(duration), 60)
        duration_str = f"{dm}:{ds:02d}"

        corrected = result.get("corrected_text", "")
        summary = result.get("summary", "")

        body = f"""
        <div class="result-header">
            <h2>✅ 转录完成</h2>
            <div class="meta">
                <span class="badge">🌐 {lang}</span>
                <span class="badge">⏱ {duration_str}</span>
                <span class="badge">📄 {len(segs)} 段</span>
            </div>
        </div>

        <div class="tabs">
            <button class="tab-btn active" onclick="switchTab('transcript')">📝 逐段文字</button>
            <button class="tab-btn" onclick="switchTab('corrected')">🔧 纠错版</button>
            <button class="tab-btn" onclick="switchTab('summary')">📋 AI 总结</button>
        </div>

        <div id="tab-transcript" class="tab-content active">
            <div class="segments-list">{seg_html}</div>
        </div>

        <div id="tab-corrected" class="tab-content">
            <div class="corrected-text">
                <pre>{corrected}</pre>
            </div>
        </div>

        <div id="tab-summary" class="tab-content">
            <div class="summary-text">
                <pre>{summary}</pre>
            </div>
        </div>

        <div class="export-bar">
            <h3>📥 导出</h3>
            <a href="/api/export/{task_id}?fmt=txt" class="btn">📄 TXT</a>
            <a href="/api/export/{task_id}?fmt=json" class="btn">📊 JSON</a>
            <a href="/api/export/{task_id}?fmt=srt" class="btn">🎬 SRT 字幕</a>
        </div>
        """
    else:
        body = f"<h2>状态: {status}</h2>"

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>转录结果 - 视频转录服务</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f0f0f;color:#e0e0e0;min-height:100vh;}}
.container{{max-width:900px;margin:0 auto;padding:24px 16px;}}
h2{{font-size:1.3rem;margin-bottom:12px;}}
.status-card{{background:#1a1a2e;border-radius:12px;padding:40px;text-align:center;margin-top:40px;}}
.status-card.error{{background:#2e1a1a;}}
.progress-bar{{height:8px;background:#333;border-radius:4px;overflow:hidden;margin:20px 0;}}
.progress-fill{{height:100%;background:#6c63ff;border-radius:4px;transition:width 0.5s;}}
.status-msg{{color:#aaa;}}
.error-msg{{color:#ff6b6b;}}
.result-header{{margin-bottom:20px;}}
.meta{{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;}}
.badge{{background:#1a1a2e;padding:4px 12px;border-radius:20px;font-size:.85rem;color:#ccc;}}
.tabs{{display:flex;gap:4px;margin-bottom:16px;flex-wrap:wrap;}}
.tab-btn{{background:#1a1a2e;color:#aaa;border:none;padding:10px 18px;border-radius:8px;cursor:pointer;font-size:.9rem;transition:all .2s;}}
.tab-btn.active{{background:#6c63ff;color:#fff;}}
.tab-btn:hover{{background:#333;}}
.tab-content{{display:none;}}
.tab-content.active{{display:block;}}
.segments-list{{max-height:600px;overflow-y:auto;}}
.segment{{display:flex;gap:10px;padding:8px 12px;border-radius:6px;margin-bottom:4px;background:#1a1a2e;}}
.segment:hover{{background:#222;}}
.ts{{color:#6c63ff;font-family:monospace;font-size:.85rem;white-space:nowrap;padding-top:1px;}}
.txt{{color:#d0d0d0;line-height:1.6;}}
.corrected-text pre,.summary-text pre{{background:#1a1a2e;padding:20px;border-radius:8px;line-height:1.8;white-space:pre-wrap;font-family:inherit;font-size:.95rem;}}
.export-bar{{background:#1a1a2e;border-radius:12px;padding:20px;margin-top:20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}}
.export-bar h3{{margin:0;margin-right:8px;font-size:1rem;}}
.btn{{display:inline-block;background:#6c63ff;color:#fff;padding:8px 20px;border-radius:6px;text-decoration:none;font-size:.9rem;transition:background .2s;}}
.btn:hover{{background:#5a52e0;}}
.more{{color:#888;text-align:center;padding:12px;font-style:italic;}}
</style>
<script>
function switchTab(name){{
    document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    document.getElementById('tab-'+name).classList.add('active');
    document.querySelector(`.tab-btn[onclick="switchTab('${{name}}')"]`).classList.add('active');
}}
</script>
</head><body><div class="container">{body}</div></body></html>""")

# ============================================================
# 后台清理（定期删除旧文件）
# ============================================================
async def cleanup_loop():
    while True:
        await asyncio.sleep(3600)
        now = time.time()
        for task_id, t in list(tasks.items()):
            if t.get("created_at", 0) < now - CLEANUP_HOURS * 3600:
                del tasks[task_id]
                rfile = RESULTS_DIR / f"{task_id}.json"
                if rfile.exists():
                    rfile.unlink()
        save_tasks()

@app.on_event("startup")
async def startup():
    asyncio.create_task(cleanup_loop())

# ============================================================
# 管理 API
# ============================================================
@app.get("/admin/keys")
async def list_keys(key: str = Query(...)):
    if key != os.environ.get("ADMIN_KEY", "admin-secret"):
        raise HTTPException(403, "无权限")
    return [{"key": k, **v} for k, v in API_KEYS.items()]

@app.post("/admin/keys")
async def add_key(key: str = Query(...), name: str = Query(...)):
    if key != os.environ.get("ADMIN_KEY", "admin-secret"):
        raise HTTPException(403, "无权限")
    new_key = uuid.uuid4().hex[:16]
    API_KEYS[new_key] = {"name": name, "created_at": time.time(), "used": 0}
    save_api_keys()
    return {"key": new_key, "name": name}

# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)
