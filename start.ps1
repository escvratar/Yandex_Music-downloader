$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

function Write-Section([string]$Text) {
    Write-Host ""
    Write-Host "============================================================"
    Write-Host "  $Text"
    Write-Host "============================================================"
}

function Test-Python {
    try {
        $null = & python --version
    } catch {
        throw "Python не найден. Установите Python и добавьте его в PATH."
    }
}

function Ensure-Dependencies {
    Write-Host "Проверка зависимостей..."
    try {
        $null = & python -c "import yandex_music, tqdm, mutagen"
        Write-Host "[OK] Зависимости уже установлены."
    } catch {
        Write-Host "Установка зависимостей из requirements.txt..."
        & python -m pip install -r (Join-Path $scriptDir "requirements.txt")
        if ($LASTEXITCODE -ne 0) {
            throw "Не удалось установить зависимости."
        }
        Write-Host "[OK] Зависимости установлены."
    }
}

function Read-Choice {
    Write-Host ""
    Write-Host "Выберите действие:"
    Write-Host "  0. Выход"
    Write-Host "  1. Только авторизация и сохранение токена"
    Write-Host "  2. Экспорт списка треков в CSV"
    Write-Host "  3. Экспорт списка треков в JSON"
    Write-Host "  4. Скачать всю библиотеку в MP3"
    Write-Host "  5. Скачать всю библиотеку в FLAC/Lossless"
    Write-Host "  6. Экспорт в CSV и скачивание MP3"
    Write-Host "  7. Экспорт в JSON и скачивание FLAC/Lossless"
    do {
        $choice = Read-Host "Введите номер (0-7)"
    } until ($choice -match '^[0-7]$')
    return [int]$choice
}

function Wait-ForMenuReturn {
    Write-Host ""
    [void](Read-Host "Нажмите Enter, чтобы вернуться в главное меню")
}

function Read-OptionalValue([string]$Prompt, [string]$DefaultValue) {
    $value = Read-Host "$Prompt [$DefaultValue]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }
    return $value
}

function Read-OptionalLimit {
    $raw = Read-Host "Ограничить число треков для теста? Нажмите Enter, чтобы скачать все"
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }
    if ($raw -match '^\d+$' -and [int]$raw -gt 0) {
        return [int]$raw
    }
    Write-Host "Некорректное значение. Ограничение применяться не будет."
    return $null
}

function Read-OptionalConcurrency {
    $raw = Read-Host "Сколько треков скачивать одновременно? [1]"
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return 1
    }
    if ($raw -match '^\d+$' -and [int]$raw -gt 0) {
        return [int]$raw
    }
    Write-Host "Некорректное значение. Используется 1."
    return 1
}

function Read-YesNo([string]$Prompt, [bool]$DefaultValue = $true) {
    $suffix = if ($DefaultValue) { "[Y/n]" } else { "[y/N]" }
    $raw = (Read-Host "$Prompt $suffix").Trim().ToLower()

    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $DefaultValue
    }

    if ($raw -in @("y", "yes", "д", "да")) {
        return $true
    }

    if ($raw -in @("n", "no", "н", "нет")) {
        return $false
    }

    Write-Host "Некорректный ответ. Будет использовано значение по умолчанию."
    return $DefaultValue
}

function Invoke-Downloader([string[]]$DownloaderArgs) {
    Write-Host ""
    Write-Host "Запуск: python downloader.py $($DownloaderArgs -join ' ')"
    & python (Join-Path $scriptDir "downloader.py") @DownloaderArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Команда завершилась с ошибкой."
    }
}

try {
    Write-Section "Полный запуск Яндекс.Музыки"
    Test-Python
    Write-Host "[OK] Python найден."
    Ensure-Dependencies

    $tokenFile = Join-Path $scriptDir "token.txt"
    $commonArgs = @("--token-file", $tokenFile)

    while ($true) {
        $choice = Read-Choice

        if ($choice -eq 0) {
            Write-Host ""
            Write-Host "Выход из программы."
            break
        }

        switch ($choice) {
            1 {
                Invoke-Downloader ($commonArgs + @("--auth"))
            }
            2 {
                Invoke-Downloader ($commonArgs + @("--list", "--export-format", "csv"))
            }
            3 {
                Invoke-Downloader ($commonArgs + @("--list", "--export-format", "json"))
            }
            4 {
                $output = Read-OptionalValue "Папка для сохранения музыки" (Join-Path $scriptDir "YandexMusic")
                $limit = Read-OptionalLimit
                $concurrency = Read-OptionalConcurrency
                $withCovers = Read-YesNo "Скачивать обложки?" $true
                $args = $commonArgs + @("--download", "--quality", "mp3", "--output", $output)
                if ($null -ne $limit) {
                    $args += @("--limit", "$limit")
                }
                if ($concurrency -gt 1) {
                    $args += @("--concurrency", "$concurrency")
                }
                if ($withCovers) {
                    $args += "--with-covers"
                }
                Invoke-Downloader $args
            }
            5 {
                $output = Read-OptionalValue "Папка для сохранения музыки" (Join-Path $scriptDir "YandexMusic")
                $limit = Read-OptionalLimit
                $concurrency = Read-OptionalConcurrency
                $withCovers = Read-YesNo "Скачивать обложки?" $true
                $args = $commonArgs + @("--download", "--quality", "lossless", "--output", $output)
                if ($null -ne $limit) {
                    $args += @("--limit", "$limit")
                }
                if ($concurrency -gt 1) {
                    $args += @("--concurrency", "$concurrency")
                }
                if ($withCovers) {
                    $args += "--with-covers"
                }
                Invoke-Downloader $args
            }
            6 {
                $output = Read-OptionalValue "Папка для сохранения музыки" (Join-Path $scriptDir "YandexMusic")
                $limit = Read-OptionalLimit
                $concurrency = Read-OptionalConcurrency
                $withCovers = Read-YesNo "Скачивать обложки?" $true
                Invoke-Downloader ($commonArgs + @("--list", "--export-format", "csv"))
                $args = $commonArgs + @("--download", "--quality", "mp3", "--output", $output)
                if ($null -ne $limit) {
                    $args += @("--limit", "$limit")
                }
                if ($concurrency -gt 1) {
                    $args += @("--concurrency", "$concurrency")
                }
                if ($withCovers) {
                    $args += "--with-covers"
                }
                Invoke-Downloader $args
            }
            7 {
                $output = Read-OptionalValue "Папка для сохранения музыки" (Join-Path $scriptDir "YandexMusic")
                $limit = Read-OptionalLimit
                $concurrency = Read-OptionalConcurrency
                $withCovers = Read-YesNo "Скачивать обложки?" $true
                Invoke-Downloader ($commonArgs + @("--list", "--export-format", "json"))
                $args = $commonArgs + @("--download", "--quality", "lossless", "--output", $output)
                if ($null -ne $limit) {
                    $args += @("--limit", "$limit")
                }
                if ($concurrency -gt 1) {
                    $args += @("--concurrency", "$concurrency")
                }
                if ($withCovers) {
                    $args += "--with-covers"
                }
                Invoke-Downloader $args
            }
        }

        Wait-ForMenuReturn
    }
} catch {
    Write-Host ""
    Write-Host "[ОШИБКА] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
