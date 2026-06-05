#!/bin/bash
cd /home/ubuntu/video-transcribe
source .venv/bin/activate
export DASHSCOPE_API_KEY=$(grep DASHSCOPE_API_KEY /home/ubuntu/.hermes/.env | cut -d= -f2-)
# HuggingFace 国内镜像
export HF_ENDPOINT=https://hf-mirror.com
exec python3 -m app.main
