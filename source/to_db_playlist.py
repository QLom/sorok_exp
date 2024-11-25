from db_connection import get_db_connection
import json
import psycopg2
import os
import shutil
import datetime
from psycopg2.extras import execute_values

# Параметры подключения к PostgreSQL
DB_NAME = "music_data"
DB_USER = "postgres"
DB_PASSWORD = "dslf;sdjfk25089FDAJDLKF352*"
DB_HOST = "localhost"

def load_to_db():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    versions_folder = os.path.join(project_root, "versions")
    os.makedirs(versions_folder, exist_ok=True)

    last_file_path = os.path.join(project_root, "last_file.txt")
    with open(last_file_path, "r", encoding="utf-8") as f:
        json_files = [line.strip() for line in f.readlines()]

    conn = get_db_connection()

    cursor = conn.cursor()

    for json_file_path in json_files:
        print(f"Обрабатывается файл: {json_file_path}")
        with open(json_file_path, "r", encoding="utf-8") as f:
            tracks = json.load(f)

        file_name = os.path.basename(json_file_path)
        user_id, playlist_title = file_name.split('_', 1)
        playlist_title = playlist_title.replace('_tracks.json', '').replace('_', ' ')

        cursor.execute("SELECT id FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        if user is None:
            cursor.execute("INSERT INTO users (user_id) VALUES (%s) RETURNING id", (user_id,))
            user_id_in_db = cursor.fetchone()[0]
            print(f"Добавлен новый пользователь: {user_id}")
        else:
            user_id_in_db = user[0]
            print(f"Пользователь найден: {user_id}")

        cursor.execute("SELECT id FROM playlists WHERE title = %s AND user_id = %s", (playlist_title, user_id_in_db))
        playlist = cursor.fetchone()
        if playlist is None:
            cursor.execute("""
                INSERT INTO playlists (title, user_id) VALUES (%s, %s) RETURNING id
            """, (playlist_title, user_id_in_db))
            playlist_id = cursor.fetchone()[0]
            print(f"Добавлен новый плейлист: {playlist_title}")
        else:
            playlist_id = playlist[0]
            print(f"Плейлист найден: {playlist_title}")

        # Сравнение треков
        cursor.execute("""
            SELECT title, array_agg(name ORDER BY name) AS artists, album, duration
            FROM tracks
            LEFT JOIN artists ON tracks.id = artists.track_id
            WHERE tracks.playlist_id = %s
            GROUP BY tracks.id, title, album, duration
        """, (playlist_id,))
        existing_tracks = {
            (row[0], tuple(sorted(row[1])), row[2], row[3]) for row in cursor.fetchall()
        }

        new_tracks = {
            (track["title"], tuple(sorted(track["artists"])), track["album"], track["duration"]) for track in tracks
        }

        tracks_to_add = new_tracks - existing_tracks
        tracks_to_delete = existing_tracks - new_tracks

        # Отладочная информация
        print(f"Текущие треки в базе: {existing_tracks}")
        print(f"Новые треки из JSON: {new_tracks}")
        print(f"Треки для добавления: {tracks_to_add}")
        print(f"Треки для удаления: {tracks_to_delete}")

        # Оптимизированное добавление треков
        if tracks_to_add:
            # Подготавливаем данные для массовой вставки
            tracks_values = [(
                track["title"],
                track["duration"],
                playlist_title,
                track["album"],
                track["year"],
                playlist_id
            ) for track in tracks if (
                track["title"],
                tuple(sorted(track["artists"])),
                track["album"],
                track["duration"]
            ) in tracks_to_add]

            # Массовая вставка треков
            cursor.execute("""
                CREATE TEMPORARY TABLE temp_tracks (
                    title TEXT,
                    duration INTEGER,
                    playlist TEXT,
                    album TEXT,
                    year INTEGER,
                    playlist_id INTEGER
                ) ON COMMIT DROP
            """)

            # Используем execute_values для быстрой вставки
            from psycopg2.extras import execute_values
            execute_values(
                cursor,
                """
                INSERT INTO temp_tracks (title, duration, playlist, album, year, playlist_id)
                VALUES %s
                """,
                tracks_values
            )

            # Вставляем треки и получаем их ID
            cursor.execute("""
                INSERT INTO tracks (title, duration, playlist, album, year, playlist_id)
                SELECT title, duration, playlist, album, year, playlist_id FROM temp_tracks
                RETURNING id, title
            """)
            new_track_ids = cursor.fetchall()

            # Подготавливаем данные для массовой вставки артистов
            artists_values = []
            for track in tracks:
                if (track["title"], tuple(sorted(track["artists"])), track["album"], track["duration"]) in tracks_to_add:
                    track_id = next(id for id, title in new_track_ids if title == track["title"])
                    artists_values.extend([
                        (artist, track_id, playlist_id)
                        for artist in track["artists"]
                    ])

            # Массовая вставка артистов
            if artists_values:
                execute_values(
                    cursor,
                    """
                    INSERT INTO artists (name, track_id, playlist_id)
                    VALUES %s
                    """,
                    artists_values
                )

            # Массовая вставка версий
            versions_values = [(playlist_id, 'added', title) for _, title in new_track_ids]
            if versions_values:
                execute_values(
                    cursor,
                    """
                    INSERT INTO playlist_versions (playlist_id, change_type, track_title)
                    VALUES %s
                    """,
                    versions_values
                )

        # Удаление старых треков
        for title, artists, album, duration in tracks_to_delete:
            cursor.execute("""
                DELETE FROM artists
                WHERE track_id = (
                    SELECT id FROM tracks
                    WHERE title = %s AND album = %s AND duration = %s AND playlist_id = %s
                )
            """, (title, album, duration, playlist_id))
            print(f"Удалены исполнители для трека: {title} - {album}")

            cursor.execute("""
                DELETE FROM tracks
                WHERE title = %s AND album = %s AND duration = %s AND playlist_id = %s
            """, (title, album, duration, playlist_id))
            print(f"Удалён трек: {title} - {album}")

            # Фиксация удаления трека
            cursor.execute("""
                INSERT INTO playlist_versions (playlist_id, change_type, track_title) 
                VALUES (%s, 'removed', %s)
            """, (playlist_id, title))

        # Фиксация загрузки оригинального плейлиста
        if not existing_tracks:
            cursor.execute("""
                INSERT INTO playlist_versions (playlist_id, change_type, track_title) 
                VALUES (%s, 'loaded', NULL)
            """, (playlist_id,))

    # Очистка last_file.txt после обработки
    open(last_file_path, "w").close()
    conn.commit()
    cursor.close()
    conn.close()
    print("Все плейлисты успешно загружены в базу!")
