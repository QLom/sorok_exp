from dotenv import load_dotenv
from db_connection import get_db_connection
import yandex_music
import re
import json
import psycopg2
import os

# Токен для авторизации
load_dotenv()
TOKEN = os.getenv("YANDEX_MUSIC_TOKEN")

# Инициализация клиента
client = yandex_music.Client(TOKEN).init()
print("Подключение к Яндекс Музыке успешно!")

# Параметры подключения к PostgreSQL
DB_NAME = "music_data"
DB_USER = "postgres"
DB_PASSWORD = "dslf;sdjfk25089FDAJDLKF352*"
DB_HOST = "localhost"

def parse_playlist(link):
    # Шаблон для извлечения user_id и kind из ссылки
    def parse_playlist_link(link):
        match = re.search(r"users/(?P<user_id>[^/]+)/playlists/(?P<kind>\d+)", link)
        if match:
            user_id = match.group("user_id")
            kind = int(match.group("kind"))
            return user_id, kind
        else:
            raise ValueError("Некорректная ссылка. Проверьте формат!")

    user_id, kind = parse_playlist_link(link)
    print(f"Извлечён user_id: {user_id}, kind: {kind}")
    
    playlist = client.users_playlists(user_id=user_id, kind=kind)
    print(f"Название плейлиста: {playlist.title}")

    # Сохранение данных в базу
    conn = get_db_connection()
    cursor = conn.cursor()

    # Сохранение или обновление данных о плейлисте
    cursor.execute("""
        SELECT id FROM users WHERE user_id = %s
    """, (user_id,))
    user = cursor.fetchone()
    if user is None:
        cursor.execute("INSERT INTO users (user_id) VALUES (%s) RETURNING id", (user_id,))
        user_id_in_db = cursor.fetchone()[0]
    else:
        user_id_in_db = user[0]

    cursor.execute("""
        INSERT INTO playlists (title, user_id, kind) VALUES (%s, %s, %s)
        ON CONFLICT (title, user_id, kind) DO UPDATE SET kind = EXCLUDED.kind
        RETURNING id
    """, (playlist.title, user_id_in_db, kind))
    playlist_id = cursor.fetchone()[0]

    conn.commit()
    conn.close()

    # Сохранение данных о треках в JSON
    tracks_data = []
    batch_size = 50
    
    for i in range(0, len(playlist.tracks), batch_size):
        batch = playlist.tracks[i:i + batch_size]
        print(f"Обработка треков {i+1}-{min(i+batch_size, len(playlist.tracks))} из {len(playlist.tracks)}")
        
        for track in batch:
            track_obj = track.fetch_track()
            tracks_data.append({
                "title": track_obj.title,
                "artists": [artist.name for artist in track_obj.artists],
                "duration": track_obj.duration_ms // 1000,
                "album": track_obj.albums[0].title if track_obj.albums else None,
                "year": track_obj.albums[0].year if track_obj.albums else None,
            })
    
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    output_folder = os.path.join(project_root, "data")
    os.makedirs(output_folder, exist_ok=True)

    output_file = os.path.join(output_folder, f"{user_id}_{playlist.title.replace(' ', '_')}_tracks.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(tracks_data, f, ensure_ascii=False, indent=4)

    last_file_path = os.path.join(project_root, "last_file.txt")
    with open(last_file_path, "a", encoding="utf-8") as f:
        f.write(output_file + "\n")

    print(f"Данные плейлиста сохранены в файл: {output_file}")