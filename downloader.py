#!/usr/bin/env python3
"""
Yandex Music Downloader
=======================
Скачивает музыку из вашей библиотеки Яндекс.Музыки в формате MP3 или FLAC,
а также может экспортировать список треков в CSV/JSON.

Автор: Antigravity AI
"""

import os
import sys
import re

# Принудительно UTF-8 вывод в Windows (убирает UnicodeEncodeError)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import csv
import json
import time
import argparse
import logging
from pathlib import Path
from typing import Optional

# Проверяем наличие библиотек
try:
    import yandex_music
    from yandex_music import Client
except ImportError:
    print("❌ Библиотека yandex-music не установлена!")
    print("   Запустите: pip install yandex-music")
    sys.exit(1)

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    """Убирает недопустимые символы из имени файла."""
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = name.strip('. ')
    return name[:200] if name else "неизвестно"


def get_artists_str(track) -> str:
    """Возвращает строку с именами исполнителей через запятую."""
    if track.artists:
        return ", ".join(a.name for a in track.artists if a.name)
    return "Неизвестный исполнитель"


def get_album_str(track) -> str:
    """Возвращает название альбома трека."""
    if track.albums:
        return track.albums[0].title or "Неизвестный альбом"
    return "Неизвестный альбом"


def get_year_str(track) -> str:
    """Возвращает год выпуска трека."""
    if track.albums and track.albums[0].year:
        return str(track.albums[0].year)
    return ""


def format_duration(seconds: int) -> str:
    """Возвращает длительность в формате ММ:СС или ЧЧ:ММ:СС."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{secs:02}"
    return f"{minutes:02}:{secs:02}"


def make_export_row(track) -> dict:
    """Собирает красивую строку экспорта без технических полей."""
    return {
        "Название": track.title or "",
        "Исполнитель": get_artists_str(track),
        "Альбом": get_album_str(track),
        "Год": get_year_str(track),
        "Длительность": format_duration((track.duration_ms or 0) // 1000),
    }


def save_album_cover(track, track_dir: Path) -> None:
    """Сохраняет обложку альбома в папку альбома, если она доступна."""
    cover_path = track_dir / "cover.jpg"
    if cover_path.exists():
        return

    cover_source = None
    if track.albums and track.albums[0]:
        cover_source = track.albums[0]
    elif getattr(track, "cover_uri", None):
        cover_source = track

    if cover_source is None:
        return

    try:
        cover_source.download_cover(str(cover_path), size="1000x1000")
    except Exception:
        try:
            cover_source.download_cover(str(cover_path), size="400x400")
        except Exception as e:
            log.debug(f"Не удалось сохранить обложку для '{track_dir.name}': {e}")


def download_track(
    track,
    output_dir: Path,
    fmt: str = "mp3",
    skip_existing: bool = True,
    with_covers: bool = False,
) -> bool:
    """
    Скачивает один трек.
    
    Args:
        track: объект трека yandex_music
        output_dir: папка для сохранения
        fmt: формат ("mp3" или "lossless" для FLAC)
        skip_existing: пропускать уже скачанные треки
    
    Returns:
        True если трек скачан успешно
    """
    artists = get_artists_str(track)
    title = track.title or "Без названия"
    album = get_album_str(track)

    # Создаём подпапку Исполнитель/Альбом
    artist_safe = sanitize_filename(artists.split(",")[0].strip())
    album_safe = sanitize_filename(album)
    track_dir = output_dir / artist_safe / album_safe
    track_dir.mkdir(parents=True, exist_ok=True)
    if with_covers:
        save_album_cover(track, track_dir)

    filename = f"{sanitize_filename(artists)} - {sanitize_filename(title)}"
    
    # Определяем расширение
    ext = "mp3"  # по умолчанию
    
    # Пробуем получить инфо о скачивании
    try:
        download_infos = track.get_download_info()
    except Exception as e:
        log.warning(f"Не удалось получить информацию о скачивании для '{title}': {e}")
        return False

    if not download_infos:
        log.warning(f"Нет доступных для скачивания форматов для '{title}'")
        return False

    # Выбираем лучший доступный формат
    chosen = None
    if fmt == "lossless":
        # Ищем FLAC
        flac_infos = [d for d in download_infos if d.codec == "flac"]
        if flac_infos:
            chosen = max(flac_infos, key=lambda d: d.bitrate_in_kbps or 0)
            ext = "flac"
        else:
            # Fallback на MP3
            mp3_infos = [d for d in download_infos if d.codec == "mp3"]
            if mp3_infos:
                chosen = max(mp3_infos, key=lambda d: d.bitrate_in_kbps or 0)
                ext = "mp3"
            else:
                chosen = max(download_infos, key=lambda d: d.bitrate_in_kbps or 0)
                ext = chosen.codec or "mp3"
    else:
        # MP3 или лучший доступный
        mp3_infos = [d for d in download_infos if d.codec == "mp3"]
        if mp3_infos:
            chosen = max(mp3_infos, key=lambda d: d.bitrate_in_kbps or 0)
            ext = "mp3"
        else:
            chosen = max(download_infos, key=lambda d: d.bitrate_in_kbps or 0)
            ext = chosen.codec or "mp3"

    if not chosen:
        log.warning(f"Не удалось выбрать формат для '{title}'")
        return False

    filepath = track_dir / f"{filename}.{ext}"

    if skip_existing and filepath.exists():
        log.debug(f"Пропуск (уже скачан): {filepath.name}")
        return True

    try:
        chosen.download(str(filepath))
        log.info(f"✅ Скачан: {artists} - {title} [{ext.upper()}, {chosen.bitrate_in_kbps}kbps]")
        return True
    except Exception as e:
        log.error(f"❌ Ошибка скачивания '{title}': {e}")
        # Удаляем неполный файл
        if filepath.exists():
            filepath.unlink()
        return False


def export_library(client: Client, output_file: Path, export_format: str = "csv"):
    """
    Экспортирует список всей библиотеки в CSV или JSON файл.
    """
    log.info("📚 Загружаем вашу библиотеку с Яндекс.Музыки...")
    
    all_tracks = []
    
    # Получаем лайкнутые треки
    try:
        liked = client.users_likes_tracks()
        if liked and liked.tracks:
            log.info(f"  🎵 Лайкнутые треки: {len(liked.tracks)}")
            
            # Загружаем полную информацию о треках батчами
            track_ids = [f"{t.id}:{t.album_id}" if t.album_id else str(t.id) for t in liked.tracks]
            
            batch_size = 100
            for i in range(0, len(track_ids), batch_size):
                batch = track_ids[i:i+batch_size]
                try:
                    tracks_info = client.tracks(batch)
                    for track in tracks_info:
                        if track and track.available:
                            all_tracks.append({
                                "track_id": track.id,
                                "export_row": make_export_row(track),
                            })
                except Exception as e:
                    log.warning(f"Ошибка при загрузке батча треков: {e}")
                time.sleep(0.2)
    except Exception as e:
        log.warning(f"Не удалось загрузить лайкнутые треки: {e}")

    # Получаем плейлисты пользователя
    try:
        playlists = client.users_playlists_list()
        log.info(f"  📋 Плейлисты: {len(playlists)}")
        for playlist in playlists:
            try:
                pl_full = client.users_playlists(playlist.kind)
                if pl_full and pl_full.tracks:
                    for short_track in pl_full.tracks:
                        if short_track.track and short_track.track.available:
                            t = short_track.track
                            all_tracks.append({
                                "track_id": t.id,
                                "export_row": make_export_row(t),
                            })
                time.sleep(0.3)
            except Exception as e:
                log.warning(f"Не удалось загрузить плейлист '{playlist.title}': {e}")
    except Exception as e:
        log.warning(f"Не удалось загрузить плейлисты: {e}")

    # Удаляем дубликаты по track_id
    seen_ids = set()
    unique_tracks = []
    for t in all_tracks:
        tid = str(t.get("track_id", ""))
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            unique_tracks.append(t["export_row"])
        elif not tid:
            unique_tracks.append(t["export_row"])

    log.info(f"\n📊 Всего уникальных треков: {len(unique_tracks)}")

    # Экспортируем
    if export_format == "csv":
        with open(output_file, "w", encoding="utf-8-sig", newline="") as f:
            if unique_tracks:
                writer = csv.DictWriter(
                    f,
                    fieldnames=list(unique_tracks[0].keys()),
                    delimiter=";",
                )
                writer.writeheader()
                writer.writerows(unique_tracks)
        log.info(f"✅ Список сохранён в: {output_file}")
    elif export_format == "json":
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(unique_tracks, f, ensure_ascii=False, indent=2)
        log.info(f"✅ Список сохранён в: {output_file}")

    return unique_tracks


def download_library(client: Client, output_dir: Path, fmt: str = "mp3", 
                     skip_existing: bool = True, limit: Optional[int] = None,
                     with_covers: bool = False):
    """
    Скачивает всю библиотеку пользователя.
    """
    log.info("🎵 Получаем список треков для скачивания...")
    
    # Получаем лайкнутые треки
    try:
        liked = client.users_likes_tracks()
    except Exception as e:
        log.error(f"Не удалось получить список лайкнутых треков: {e}")
        return

    if not liked or not liked.tracks:
        log.warning("Нет лайкнутых треков.")
        return

    track_shorts = liked.tracks
    if limit:
        track_shorts = track_shorts[:limit]

    total = len(track_shorts)
    log.info(f"📦 Треков для скачивания: {total}")
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    success = 0
    failed = 0
    skipped = 0

    for i, short_track in enumerate(track_shorts, 1):
        print(f"\r[{i}/{total}] ", end="", flush=True)
        
        try:
            track_id = f"{short_track.id}:{short_track.album_id}" if short_track.album_id else str(short_track.id)
            tracks = client.tracks([track_id])
            if not tracks:
                failed += 1
                continue
            
            track = tracks[0]
            if not track.available:
                log.warning(f"Трек недоступен: {track.title}")
                failed += 1
                continue

            result = download_track(
                track,
                output_dir,
                fmt=fmt,
                skip_existing=skip_existing,
                with_covers=with_covers,
            )
            if result:
                # Проверяем, был ли пропущен
                artists = get_artists_str(track)
                title = track.title or "Без названия"
                album = get_album_str(track)
                artist_safe = sanitize_filename(artists.split(",")[0].strip())
                album_safe = sanitize_filename(album)
                filename = f"{sanitize_filename(artists)} - {sanitize_filename(title)}"
                ext = "flac" if fmt == "lossless" else "mp3"
                filepath = output_dir / artist_safe / album_safe / f"{filename}.{ext}"
                if filepath.exists() and skip_existing:
                    # Уже существовал до начала (логируется как success тоже)
                    pass
                success += 1
            else:
                failed += 1
                
        except Exception as e:
            log.error(f"Ошибка обработки трека: {e}")
            failed += 1
        
        # Небольшая задержка чтобы не перегружать API
        time.sleep(0.5)

    print()  # перевод строки после \r
    log.info(f"\n{'='*50}")
    log.info(f"✅ Успешно скачано: {success}")
    log.info(f"❌ Ошибок:         {failed}")
    log.info(f"📁 Папка:          {output_dir}")
    log.info(f"{'='*50}")


def get_token_from_browser() -> str:
    """Получает токен через Device Flow, с ручным вводом как запасным вариантом."""
    import webbrowser

    print()
    print("=" * 60)
    print("  АВТОРИЗАЦИЯ ЯНДЕКС.МУЗЫКИ")
    print("=" * 60)
    print("""
Сейчас программа попробует открыть безопасную авторизацию через код.

Что нужно будет сделать:
  1. Открыть страницу подтверждения Яндекса
  2. Ввести код, который покажет программа
  3. Нажать "Разрешить"
  4. Подождать, пока программа автоматически получит токен

Если автоматический способ не сработает, ниже будет запасной ручной ввод токена.
""")

    try:
        open_b = input("Открыть страницу авторизации в браузере? [Y/n]: ").strip().lower()
    except EOFError:
        print("\n[ОШИБКА] Не удалось прочитать ответ из консоли.")
        return ""

    auto_open = open_b in ("", "y", "yes", "да", "д")

    try:
        auth_client = Client()
        code = auth_client.request_device_code()

        print()
        print("Код подтверждения:", code.user_code, flush=True)
        print("Ссылка для входа:", code.verification_url, flush=True)
        print(f"Код действует примерно {code.expires_in} сек.", flush=True)
        if auto_open:
            webbrowser.open(code.verification_url)
            print("Страница авторизации открыта в браузере.", flush=True)
        print("Введите этот код на странице Яндекса и нажмите «Разрешить».", flush=True)
        print("Ожидание подтверждения...", flush=True)
        print()

        deadline = time.time() + min(code.expires_in, 600)
        interval = max(1, int(code.interval))
        while time.time() < deadline:
            token_obj = auth_client.poll_device_token(code.device_code)
            if token_obj and token_obj.access_token:
                print("✅ Токен успешно получен автоматически.")
                return token_obj.access_token.strip()
            time.sleep(interval)

        print("\n[ПРЕДУПРЕЖДЕНИЕ] Время ожидания подтверждения истекло.")
    except Exception as e:
        print(f"\n[ПРЕДУПРЕЖДЕНИЕ] Автоматическая авторизация не сработала: {e}")

    print()
    print("Резервный вариант: вставьте токен вручную, если он у вас уже есть.")
    try:
        token = input("Введите токен и нажмите Enter: ").strip()
    except EOFError:
        print("\n[ОШИБКА] Не удалось прочитать токен из консоли.")
        return ""

    if token.lower().startswith("oauth "):
        token = token[6:].strip()

    return token


def main():
    parser = argparse.ArgumentParser(
        description="[Яндекс.Музыка Загрузчик]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  # Авторизация и экспорт списка треков в CSV:
  python downloader.py --list --export-format csv

  # Скачать всю библиотеку в MP3:
  python downloader.py --download --quality mp3

  # Скачать в FLAC (если доступно):
  python downloader.py --download --quality lossless

  # Скачать только первые 10 треков (для теста):
  python downloader.py --download --quality mp3 --limit 10

  # Указать токен напрямую:
  python downloader.py --token YOUR_TOKEN --download
        """
    )
    
    parser.add_argument("--token", type=str, help="Токен Яндекс.Музыки (если уже есть)")
    parser.add_argument("--token-file", type=str, default="token.txt",
                        help="Файл для сохранения/загрузки токена (по умолчанию: token.txt)")
    
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument("--download", action="store_true",
                            help="Скачать музыку из библиотеки")
    mode_group.add_argument("--list", action="store_true",
                            help="Экспортировать список треков (без скачивания)")
    mode_group.add_argument("--auth", action="store_true",
                            help="Только получить и сохранить токен авторизации")
    
    parser.add_argument("--quality", choices=["mp3", "lossless"], default="mp3",
                        help="Качество скачивания: mp3 или lossless/FLAC (по умолчанию: mp3)")
    parser.add_argument("--output", type=str, default="Яндекс.Музыка",
                        help="Папка для сохранения музыки (по умолчанию: Яндекс.Музыка)")
    parser.add_argument("--export-format", choices=["csv", "json"], default="csv",
                        help="Формат экспорта списка: csv или json (по умолчанию: csv)")
    parser.add_argument("--no-skip", action="store_true",
                        help="Не пропускать уже скачанные файлы (перезаписывать)")
    parser.add_argument("--with-covers", action="store_true",
                        help="Сохранять обложки альбомов в папках с музыкой")
    parser.add_argument("--limit", type=int, default=None,
                        help="Ограничить количество треков (для тестирования)")
    parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    print("=" * 60)
    print("  Загрузчик Яндекс.Музыки v1.0")
    print("=" * 60)

    # Получаем токен
    token = args.token
    token_file = Path(args.token_file)

    if not token and token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            log.info(f"🔑 Токен загружен из файла: {token_file}")

    if not token:
        print("\nТокен не найден. Нужна авторизация.")
        token = get_token_from_browser()

        if not token:
            print("\n[ОШИБКА] Токен не введён.")
            print("   Запустите снова и введите токен, или используйте флаг --token YOUR_TOKEN")
            sys.exit(1)

    # Авторизуемся
    try:
        log.info("🔐 Авторизация...")
        client = Client(token).init()
        account = client.me.account
        log.info(f"✅ Авторизован как: {account.display_name} ({account.login})")
    except Exception as e:
        log.error(f"❌ Ошибка авторизации: {e}")
        if "Unauthorized" in str(e) or "401" in str(e):
            log.error("   Токен недействителен. Получите новый токен.")
        sys.exit(1)

    # Сохраняем токен
    if token:
        token_file.write_text(token, encoding="utf-8")
        log.info(f"💾 Токен сохранён в: {token_file}")

    if args.auth:
        print(f"\n✅ Авторизация успешна! Токен сохранён в: {token_file}")
        return

    if args.list:
        export_format = args.export_format
        output_file = Path(f"library.{export_format}")
        tracks = export_library(client, output_file, export_format)
        
        # Выводим краткую статистику
        if tracks:
            print(f"\n📊 Статистика библиотеки:")
            
            artists_count = len(set(t["Исполнитель"] for t in tracks))
            albums_count = len(set(t["Альбом"] for t in tracks))
            total_duration = 0
            for t in tracks:
                duration_text = t.get("Длительность", "00:00")
                parts = [int(part) for part in duration_text.split(":")]
                if len(parts) == 2:
                    total_duration += parts[0] * 60 + parts[1]
                elif len(parts) == 3:
                    total_duration += parts[0] * 3600 + parts[1] * 60 + parts[2]
            hours = total_duration // 3600
            minutes = (total_duration % 3600) // 60
            
            print(f"   🎵 Треков:       {len(tracks)}")
            print(f"   👤 Исполнителей: {artists_count}")
            print(f"   💿 Альбомов:     {albums_count}")
            print(f"   ⏱️  Длительность: {hours}ч {minutes}мин")
            print(f"\n   📁 Файл: {output_file.absolute()}")

    elif args.download:
        output_dir = Path(args.output)
        download_library(
            client=client,
            output_dir=output_dir,
            fmt=args.quality,
            skip_existing=not args.no_skip,
            limit=args.limit,
            with_covers=args.with_covers,
        )


if __name__ == "__main__":
    main()
