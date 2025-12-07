# Image Optimization Script for Windows
# This script helps optimize images for better website performance

Write-Host "=== Image Optimization Guide ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "OPTION 1: Using PowerShell with System.Drawing (Built-in)" -ForegroundColor Yellow
Write-Host "This option uses built-in Windows tools but may have limited WebP support."
Write-Host ""

# Create optimized-images directory if it doesn't exist
$sourceDir = "d:\backup\Documents\baaz-meetings\frontend"
$optimizedDir = Join-Path $sourceDir "optimized-images"

if (!(Test-Path $optimizedDir)) {
    New-Item -ItemType Directory -Path $optimizedDir -Force | Out-Null
    Write-Host "Created directory: $optimizedDir" -ForegroundColor Green
}

Write-Host ""
Write-Host "OPTION 2: Using Online Tools (Recommended)" -ForegroundColor Yellow
Write-Host "For best results, use online image optimization tools:"
Write-Host "  - https://squoosh.app/ (Google's image optimizer - supports WebP)"
Write-Host "  - https://tinypng.com/ (PNG/JPG compression)"
Write-Host "  - https://cloudconvert.com/webp-converter (Format conversion)"
Write-Host ""

Write-Host "OPTION 3: Install ImageMagick (Professional Tool)" -ForegroundColor Yellow
Write-Host "Install from: https://imagemagick.org/script/download.php#windows"
Write-Host "Then run these commands:"
Write-Host ""
Write-Host "  # Navigate to frontend directory"
Write-Host "  cd ""$sourceDir"""
Write-Host ""
Write-Host "  # Convert and optimize each image"
Write-Host "  magick New.jpg -quality 70 -resize 1920x1080> optimized-images/New.webp"
Write-Host "  magick Newbaaz.png -quality 70 optimized-images/Newbaaz.webp"
Write-Host "  magick baazlogo.png -quality 70 optimized-images/baazlogo.webp"
Write-Host "  magick Baaz_Logo.png -quality 70 optimized-images/Baaz_Logo.webp"
Write-Host "  magick baaz1.png -quality 70 optimized-images/baaz1.webp"
Write-Host "  magick ""wall 4.webp"" -quality 60 optimized-images/wall-4-optimized.webp"
Write-Host ""

Write-Host "=== Current Image Sizes ===" -ForegroundColor Cyan
Get-ChildItem -Path $sourceDir -Filter "*.png","*.jpg","*.webp" | 
    Where-Object { $_.Name -match '\.(png|jpg|webp)$' } |
    Select-Object Name, @{Name="Size(KB)";Expression={[math]::Round($_.Length/1KB,2)}} | 
    Sort-Object -Property 'Size(KB)' -Descending |
    Format-Table -AutoSize

Write-Host ""
Write-Host "=== Manual Steps ===" -ForegroundColor Yellow
Write-Host "1. Optimize images using one of the methods above"
Write-Host "2. Save optimized images to: $optimizedDir"
Write-Host "3. Update HTML files to reference optimized images"
Write-Host "4. Test the website to ensure images load correctly"
Write-Host ""

Write-Host "=== Quick Win with Built-in Tools ===" -ForegroundColor Green
Write-Host "I can help you create reduced-size copies using PowerShell."
Write-Host "While not WebP, this will still improve load times."
Write-Host ""

# Function to optimize images using .NET (limited functionality)
function Optimize-ImageWithDotNet {
    param(
        [string]$SourcePath,
        [string]$DestPath,
        [int]$Quality = 70
    )
    
    Add-Type -AssemblyName System.Drawing
    
    $img = [System.Drawing.Image]::FromFile($SourcePath)
    $encoder = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' }
    $encoderParams = New-Object System.Drawing.Imaging.EncoderParameters(1)
    $encoderParams.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, $Quality)
    
    $img.Save($DestPath, $encoder, $encoderParams)
    $img.Dispose()
}

Write-Host "Would you like to proceed with the recommended approach?" -ForegroundColor Cyan
Write-Host "RECOMMENDED: Use https://squoosh.app/ for best results" -ForegroundColor Yellow
Write-Host ""
Write-Host "This script has created the 'optimized-images' directory for you."
Write-Host "Upload your images to Squoosh.app and download optimized WebP versions there."
