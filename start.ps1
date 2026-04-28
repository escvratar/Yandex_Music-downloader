$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [System.Text.UTF8Encoding]::new()

# Принуждаем Python писать UTF-8 (лечит "иероглифы" в консоли)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Для Windows PowerShell (5.x) иногда помогает явно переключить кодовую страницу консоли
try { & chcp 65001 | Out-Null } catch { }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

function Get-UiMap {
    $uiPath = Join-Path $scriptDir "ui-ru.txt"
    $map = @{}

    if (-not (Test-Path $uiPath)) {
        return $map
    }

    $bytes = [System.IO.File]::ReadAllBytes($uiPath)
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    foreach ($line in ($text -split "`r?`n")) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line.StartsWith("#")) { continue }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { continue }
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1)
        if (-not [string]::IsNullOrWhiteSpace($key)) {
            $map[$key] = $val
        }
    }

    return $map
}

$script:Ui = Get-UiMap
function T([string]$Key) {
    if ($script:Ui.ContainsKey($Key)) { return $script:Ui[$Key] }
    return $Key
}

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
        throw (T "python_missing")
    }
}

function Ensure-Dependencies {
    Write-Host (T "deps_check")
    try {
        $null = & python -c "import yandex_music, tqdm, mutagen"
        Write-Host (T "deps_ok")
    } catch {
        Write-Host (T "deps_install")
        & python -m pip install -r (Join-Path $scriptDir "requirements.txt")
        if ($LASTEXITCODE -ne 0) {
            throw (T "deps_install_fail")
        }
        Write-Host (T "deps_installed")
    }
}

function Read-Choice {
    Write-Host ""
    Write-Host (T "menu_choose")
    Write-Host ("  " + (T "menu_exit"))
    Write-Host ("  " + (T "menu_auth"))
    Write-Host ("  " + (T "menu_csv"))
    Write-Host ("  " + (T "menu_json"))
    Write-Host ("  " + (T "menu_mp3"))
    Write-Host ("  " + (T "menu_flac"))
    Write-Host ("  " + (T "menu_csv_mp3"))
    Write-Host ("  " + (T "menu_json_flac"))
    do {
        $choice = Read-Host (T "prompt_choice")
    } until ($choice -match '^[0-7]$')
    return [int]$choice
}

function Wait-ForMenuReturn {
    Write-Host ""
    [void](Read-Host (T "prompt_enter_menu"))
}

function Read-OptionalValue([string]$Prompt, [string]$DefaultValue) {
    $value = Read-Host "$Prompt [$DefaultValue]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }
    return $value
}

function Read-OptionalLimit {
    $raw = Read-Host (T "prompt_limit")
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }
    if ($raw -match '^\d+$' -and [int]$raw -gt 0) {
        return [int]$raw
    }
    Write-Host (T "bad_limit")
    return $null
}

function Read-OptionalConcurrency {
    $raw = Read-Host (T "prompt_concurrency")
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return 1
    }
    if ($raw -match '^\d+$' -and [int]$raw -gt 0) {
        return [int]$raw
    }
    Write-Host (T "bad_concurrency")
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
    Write-Host ((T "run_cmd") + " python downloader.py " + ($DownloaderArgs -join ' '))
    & python (Join-Path $scriptDir "downloader.py") @DownloaderArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Команда завершилась с ошибкой."
    }
}

try {
    Write-Section (T "section_main")
    Test-Python
    Write-Host (T "python_found")
    Ensure-Dependencies

    $tokenFile = Join-Path $scriptDir "token.txt"
    $commonArgs = @("--token-file", $tokenFile)

    while ($true) {
        $choice = Read-Choice

        if ($choice -eq 0) {
            Write-Host ""
            Write-Host (T "exit_msg")
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
                $output = Read-OptionalValue (T "prompt_output") (Join-Path $scriptDir "YandexMusic")
                $limit = Read-OptionalLimit
                $concurrency = Read-OptionalConcurrency
                $withCovers = Read-YesNo (T "prompt_covers") $true
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
                $output = Read-OptionalValue (T "prompt_output") (Join-Path $scriptDir "YandexMusic")
                $limit = Read-OptionalLimit
                $concurrency = Read-OptionalConcurrency
                $withCovers = Read-YesNo (T "prompt_covers") $true
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
                $output = Read-OptionalValue (T "prompt_output") (Join-Path $scriptDir "YandexMusic")
                $limit = Read-OptionalLimit
                $concurrency = Read-OptionalConcurrency
                $withCovers = Read-YesNo (T "prompt_covers") $true
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
                $output = Read-OptionalValue (T "prompt_output") (Join-Path $scriptDir "YandexMusic")
                $limit = Read-OptionalLimit
                $concurrency = Read-OptionalConcurrency
                $withCovers = Read-YesNo (T "prompt_covers") $true
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
    Write-Host ((T "error_prefix") + " " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    [void](Read-Host (T "press_enter_exit"))
    exit 1
}
