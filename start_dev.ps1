# 开发启动脚本 (Windows PowerShell)
Write-Host "🚀 启动 AI Transcriber 开发服务器..." -ForegroundColor Cyan

# 检查 Python 环境
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✓ Python: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ Python 未安装，请先安装 Python 3.10+" -ForegroundColor Red
    exit 1
}

# 安装后端依赖
Write-Host "`n📦 安装后端依赖..." -ForegroundColor Yellow
Set-Location backend
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
Set-Location ..

# 检查 .env 文件
if (-not (Test-Path .env)) {
    Write-Host "`n⚠️  .env 文件不存在，从示例创建..." -ForegroundColor Yellow
    Copy-Item .env.example .env
    Write-Host "请编辑 .env 文件配置 API Key" -ForegroundColor Cyan
}

# 启动后端服务
Write-Host "`n🌐 启动后端服务 (http://localhost:8001)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd backend; python main.py"

# 启动前端 (简单HTTP服务器)
Write-Host "`n🎨 启动前端服务 (http://localhost:8080)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd frontend; python -m http.server 8080"

Write-Host "`n✅ 服务已启动！" -ForegroundColor Green
Write-Host "后端: http://localhost:8001" -ForegroundColor Cyan
Write-Host "前端: http://localhost:8080" -ForegroundColor Cyan
Write-Host "`n按任意键退出..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
