# 精简版 GPT-SoVITS 模型下载脚本
# 只下载预训练模型和 G2PWModel，不重装 PyTorch 和其他依赖
# 下载源：ModelScope（国内速度最快）
param(
    [ValidateSet("HF", "HF-Mirror", "ModelScope")]
    [string]$Source = "ModelScope"
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Set-Location ..\GPT-SoVITS

function Download-File {
    param([string]$Uri, [string]$OutFile)
    Write-Host "[INFO] Downloading: $OutFile" -ForegroundColor Green
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $OutFile -ErrorAction Stop
        Write-Host "[OK] $OutFile downloaded" -ForegroundColor Cyan
    } catch {
        Write-Host "[ERR] Failed to download $OutFile`: $($_.Exception.Message)" -ForegroundColor Red
        throw
    }
}

function Unzip-File {
    param([string]$ZipPath, [string]$DestPath)
    Write-Host "[INFO] Extracting: $ZipPath -> $DestPath" -ForegroundColor Green
    Expand-Archive -Path $ZipPath -DestinationPath $DestPath -Force
    Remove-Item $ZipPath -Force
    Write-Host "[OK] Extracted to $DestPath" -ForegroundColor Cyan
}

# 下载源选择
switch ($Source) {
    "HF" {
        $PretrainedURL = "https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/pretrained_models.zip"
        $G2PWURL       = "https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/G2PWModel.zip"
    }
    "HF-Mirror" {
        $PretrainedURL = "https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/pretrained_models.zip"
        $G2PWURL       = "https://hf-mirror.com/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/G2PWModel.zip"
    }
    "ModelScope" {
        $PretrainedURL = "https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/pretrained_models.zip"
        $G2PWURL       = "https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master/G2PWModel.zip"
    }
}

Write-Host "=== GPT-SoVITS Model Download ===" -ForegroundColor Yellow
Write-Host "Source: $Source"
Write-Host "Target: GPT_SoVITS/pretrained_models/ and GPT_SoVITS/text/G2PWModel/"
Write-Host ""

# 1. 下载预训练模型
if (-not (Test-Path "GPT_SoVITS/pretrained_models/sv")) {
    Write-Host "[1/2] Pretrained Models" -ForegroundColor Yellow
    Download-File -Uri $PretrainedURL -OutFile "pretrained_models.zip"
    Unzip-File -ZipPath "pretrained_models.zip" -DestPath "GPT_SoVITS"
    Write-Host "[DONE] Pretrained Models ready" -ForegroundColor Green
} else {
    Write-Host "[SKIP] Pretrained Models already exists" -ForegroundColor DarkGray
}

Write-Host ""

# 2. 下载 G2PWModel
if (-not (Test-Path "GPT_SoVITS/text/G2PWModel")) {
    Write-Host "[2/2] G2PWModel" -ForegroundColor Yellow
    Download-File -Uri $G2PWURL -OutFile "G2PWModel.zip"
    Unzip-File -ZipPath "G2PWModel.zip" -DestPath "GPT_SoVITS/text"
    Write-Host "[DONE] G2PWModel ready" -ForegroundColor Green
} else {
    Write-Host "[SKIP] G2PWModel already exists" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== All models downloaded ===" -ForegroundColor Green
Write-Host "You can now start GPT-SoVITS API: gpt_sovits_venv_py311\Scripts\python.exe api_v2.py"
