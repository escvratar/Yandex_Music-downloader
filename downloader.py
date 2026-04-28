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
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Tuple

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

# Теги/обложки в файл
try:
    from mutagen.id3 import ID3, APIC, COMM, ID3NoHeaderError, TCON, TDRC, TIT2, TPE1, TALB, TRCK, TPOS, TPE2
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC, Picture
    HAS_MUTAGEN = True
except Exception:
    HAS_MUTAGEN = False

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


def save_cover_to_dir(track, track_dir: Path, album_hint: str = "") -> None:
    """
    Сохраняет обложку в указанную папку.
    Если в одной папке несколько альбомов (например, структура только по исполнителю),
    стараемся делать имя файла уникальным.
    """
    base = "cover"
    if album_hint:
        base = f"cover - {sanitize_filename(album_hint)[:80]}"
    cover_path = track_dir / f"{base}.jpg"
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


def _download_cover_bytes(track) -> Optional[bytes]:
    """
    Возвращает байты обложки (JPG/PNG) максимально доступного размера.
    Не падает, если обложки нет.
    """
    cover_source = None
    if getattr(track, "albums", None) and track.albums and track.albums[0]:
        cover_source = track.albums[0]
    elif getattr(track, "cover_uri", None):
        cover_source = track
    if cover_source is None:
        return None

    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cover.jpg"
            try:
                cover_source.download_cover(str(p), size="1000x1000")
            except Exception:
                cover_source.download_cover(str(p), size="400x400")
            if p.exists():
                return p.read_bytes()
    except Exception as e:
        log.debug(f"Не удалось скачать обложку в память: {e}")
    return None


def _get_track_numbers(track) -> Tuple[str, str]:
    """
    Возвращает (track_number, disc_number) как строки для тегов.
    """
    tr = ""
    disc = ""
    try:
        if getattr(track, "albums", None) and track.albums:
            a = track.albums[0]
            pos = getattr(a, "track_position", None) or getattr(track, "track_position", None)
            if pos:
                num = getattr(pos, "index", None) or getattr(pos, "track", None) or getattr(pos, "number", None)
                if num:
                    tr = str(num)
                d = getattr(pos, "volume", None) or getattr(pos, "disc", None)
                if d:
                    disc = str(d)
    except Exception:
        pass
    return tr, disc


def _get_lyrics_text(track) -> str:
    """
    Пытается получить текст лирики, если доступно.
    """
    try:
        lyr = getattr(track, "get_lyrics", None)
        if callable(lyr):
            obj = lyr()
            if not obj:
                return ""
            # yandex-music может вернуть объект с текстом в разных полях
            for key in ("full_lyrics", "lyrics", "text"):
                val = getattr(obj, key, None)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    except Exception:
        return ""
    return ""


def embed_tags(audio_path: Path, track) -> None:
    """
    Вшивает максимально доступные теги в MP3/FLAC:
    title, artist, album, albumartist, year, tracknumber, discnumber, genre, lyrics, cover.
    """
    if not HAS_MUTAGEN:
        log.debug("Mutagen не установлен — теги не будут записаны.")
        return

    title = (track.title or "").strip()
    artists = get_artists_str(track).strip()
    album = get_album_str(track).strip()
    year = get_year_str(track).strip()
    track_no, disc_no = _get_track_numbers(track)

    album_artist = ""
    try:
        if getattr(track, "albums", None) and track.albums and getattr(track.albums[0], "artists", None):
            aa = [a.name for a in track.albums[0].artists if getattr(a, "name", None)]
            album_artist = ", ".join(aa).strip()
    except Exception:
        album_artist = ""

    genre = ""
    try:
        # у ЯМ жанры могут быть в track.genre или альбоме
        genre = (getattr(track, "genre", None) or "").strip()
        if not genre and getattr(track, "albums", None) and track.albums:
            genre = (getattr(track.albums[0], "genre", None) or "").strip()
    except Exception:
        genre = ""

    lyrics = _get_lyrics_text(track)
    cover_bytes = _download_cover_bytes(track)

    ext = audio_path.suffix.lower().lstrip(".")

    if ext == "mp3":
        try:
            try:
                tags = ID3(str(audio_path))
            except ID3NoHeaderError:
                tags = ID3()

            if title:
                tags.setall("TIT2", [TIT2(encoding=3, text=title)])
            if artists:
                tags.setall("TPE1", [TPE1(encoding=3, text=artists)])
            if album:
                tags.setall("TALB", [TALB(encoding=3, text=album)])
            if album_artist:
                tags.setall("TPE2", [TPE2(encoding=3, text=album_artist)])
            if year:
                tags.setall("TDRC", [TDRC(encoding=3, text=year)])
            if track_no:
                tags.setall("TRCK", [TRCK(encoding=3, text=track_no)])
            if disc_no:
                tags.setall("TPOS", [TPOS(encoding=3, text=disc_no)])
            if genre:
                tags.setall("TCON", [TCON(encoding=3, text=genre)])
            if lyrics:
                try:
                    from mutagen.id3 import USLT
                    tags.setall("USLT", [USLT(encoding=3, lang="rus", desc="", text=lyrics)])
                except Exception:
                    pass
                tags.setall("COMM", [COMM(encoding=3, lang="rus", desc="Lyrics", text=lyrics[:8000])])

            if cover_bytes:
                tags.delall("APIC")
                mime = "image/jpeg"
                if cover_bytes[:8].startswith(b"\x89PNG\r\n\x1a\n"):
                    mime = "image/png"
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover_bytes))

            tags.save(str(audio_path))
        except Exception as e:
            log.debug(f"Не удалось записать теги MP3 в '{audio_path.name}': {e}")

    elif ext == "flac":
        try:
            f = FLAC(str(audio_path))
            if title:
                f["title"] = title
            if artists:
                f["artist"] = artists
            if album:
                f["album"] = album
            if album_artist:
                f["albumartist"] = album_artist
            if year:
                f["date"] = year
            if track_no:
                f["tracknumber"] = track_no
            if disc_no:
                f["discnumber"] = disc_no
            if genre:
                f["genre"] = genre
            if lyrics:
                f["lyrics"] = lyrics

            if cover_bytes:
                f.clear_pictures()
                pic = Picture()
                pic.type = 3  # front cover
                pic.desc = "Cover"
                pic.data = cover_bytes
                pic.mime = "image/jpeg"
                if cover_bytes[:8].startswith(b"\x89PNG\r\n\x1a\n"):
                    pic.mime = "image/png"
                f.add_picture(pic)
            f.save()
        except Exception as e:
            log.debug(f"Не удалось записать теги FLAC в '{audio_path.name}': {e}")


def download_track(
    track,
    output_dir: Path,
    fmt: str = "mp3",
    skip_existing: bool = True,
    with_covers: bool = False,
    album_folders: bool = False,
    retries: int = 2,
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

    # Создаём подпапку Исполнитель[/Альбом]
    artist_safe = sanitize_filename(artists.split(",")[0].strip())
    if album_folders:
        album_safe = sanitize_filename(album)
        track_dir = output_dir / artist_safe / album_safe
    else:
        track_dir = output_dir / artist_safe
    track_dir.mkdir(parents=True, exist_ok=True)
    if with_covers:
        if album_folders:
            save_album_cover(track, track_dir)
        else:
            save_cover_to_dir(track, track_dir, album_hint=album)

    filename = f"{sanitize_filename(artists)} - {sanitize_filename(title)}"
    
    # Определяем расширение
    ext = "mp3"  # по умолчанию
    
    retries = max(1, int(retries))
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        # Пробуем получить инфо о скачивании (на каждой попытке — заново)
        try:
            download_infos = track.get_download_info()
        except Exception as e:
            last_error = e
            log.warning(
                f"Не удалось получить информацию о скачивании для '{title}' "
                f"(попытка {attempt}/{retries}): {e}"
            )
            if attempt < retries:
                time.sleep(1.0)
                continue
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
            # Если файл уже есть — всё равно попробуем вшить/обновить теги
            try:
                embed_tags(filepath, track)
            except Exception:
                pass
            return True

        try:
            chosen.download(str(filepath))
            embed_tags(filepath, track)
            log.info(f"✅ Скачан: {artists} - {title} [{ext.upper()}, {chosen.bitrate_in_kbps}kbps]")
            return True
        except Exception as e:
            last_error = e
            if attempt < retries:
                log.warning(
                    f"Ошибка скачивания '{title}' (попытка {attempt}/{retries}): {e}. "
                    f"Повтор через 2 сек..."
                )
            else:
                log.error(f"❌ Ошибка скачивания '{title}': {e}")

            # Удаляем неполный файл
            try:
                if filepath.exists():
                    filepath.unlink()
            except Exception:
                pass

            if attempt < retries:
                time.sleep(2.0)
                continue
            return False

    if last_error:
        log.debug(f"Скачивание не удалось после {retries} попыток: {last_error}")
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
                     with_covers: bool = False,
                     album_folders: bool = False,
                     concurrency: int = 1):
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

    # 1) Сначала загрузим полные объекты треков батчами (быстрее и стабильнее, чем в потоках)
    track_ids = [
        f"{t.id}:{t.album_id}" if getattr(t, "album_id", None) else str(t.id)
        for t in track_shorts
    ]
    full_tracks = []
    batch_size = 100
    for i in range(0, len(track_ids), batch_size):
        batch = track_ids[i:i + batch_size]
        try:
            tracks = client.tracks(batch)
            for tr in tracks or []:
                if tr and getattr(tr, "available", False):
                    full_tracks.append(tr)
        except Exception as e:
            log.warning(f"Ошибка при загрузке батча треков: {e}")
        time.sleep(0.2)

    if not full_tracks:
        log.warning("Не удалось получить доступные треки для скачивания.")
        return

    # 2) Скачивание: параллельно, если concurrency > 1
    concurrency = max(1, int(concurrency))
    planned_total = len(full_tracks)
    log.info(f"🚀 Старт скачивания: {planned_total} треков, одновременно: {concurrency}")

    def _worker(tr):
        return download_track(
            tr,
            output_dir,
            fmt=fmt,
            skip_existing=skip_existing,
            with_covers=with_covers,
            album_folders=album_folders,
            retries=2,
        )

    done = 0
    if concurrency == 1:
        for tr in full_tracks:
            done += 1
            print(f"\r[{done}/{planned_total}] ", end="", flush=True)
            try:
                if _worker(tr):
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                log.error(f"Ошибка обработки трека: {e}")
                failed += 1
            time.sleep(0.2)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = [ex.submit(_worker, tr) for tr in full_tracks]
            for fut in as_completed(futures):
                done += 1
                print(f"\r[{done}/{planned_total}] ", end="", flush=True)
                try:
                    if fut.result():
                        success += 1
                    else:
                        failed += 1
                except Exception as e:
                    log.error(f"Ошибка обработки трека: {e}")
                    failed += 1

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
    parser.add_argument("--album-folders", action="store_true",
                        help="Создавать подпапки альбомов: Исполнитель/Альбом (по умолчанию: только Исполнитель)")
    parser.add_argument("--concurrency", type=int, default=1,
                        help="Сколько треков скачивать одновременно (по умолчанию: 1)")
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
            album_folders=args.album_folders,
            concurrency=args.concurrency,
        )


if __name__ == "__main__":
    main()
