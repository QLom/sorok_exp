from flask import Flask, render_template, request, redirect, url_for, flash
from playlist_parser import parse_playlist
from to_db_playlist import load_to_db
from dotenv import load_dotenv
from db_connection import get_db_connection
import psycopg2
import os
from celery import Celery
from flask_caching import Cache

# Загрузка переменных из .env
load_dotenv()

# Параметры подключения
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")

app = Flask(__name__)

# Создание Celery для фоновой обработки длинных плейлистов
celery = Celery('tasks', broker='redis://localhost:6379/0')

cache = Cache(app, config={'CACHE_TYPE': 'simple'})

def get_playlists_from_db():
    """Получить список всех плейлистов из базы данных."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT playlists.id, playlists.title, users.user_id FROM playlists JOIN users ON playlists.user_id = users.id")
    playlists = cursor.fetchall()
    conn.close()
    print("Playlists:", playlists)  # Для отладки
    return playlists

@app.route('/', methods=['GET', 'POST'])
def index():
    """Главная страница."""
    if request.method == 'POST':
        playlist_link = request.form.get('playlist_link')
        if playlist_link:
            # Асинхронная обработка
            process_playlist_async.delay(playlist_link)
            flash('Плейлист добавлен в очередь на обработку')
            return redirect(url_for('index'))
    playlists = get_playlists_from_db()
    return render_template('index.html', playlists=playlists)

@app.route('/clear', methods=['GET'])
def clear():
    """Очистить все таблицы базы данных и удалить JSON-файлы."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("TRUNCATE TABLE artists, tracks, playlists, users RESTART IDENTITY CASCADE;")
    conn.commit()
    conn.close()

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    data_folder = os.path.join(project_root, "data")
    for file in os.listdir(data_folder):
        if file.endswith('.json'):
            os.remove(os.path.join(data_folder, file))

    return redirect(url_for('index'))

@app.route('/delete_playlist/<int:playlist_id>', methods=['POST'])
def delete_playlist(playlist_id):
    """Удаление плейлиста и связанных данных."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Удаление всех записей из playlist_versions
        cursor.execute("DELETE FROM playlist_versions WHERE playlist_id = %s", (playlist_id,))

        # Удаление всех треков и артистов, связанных с плейлистом
        cursor.execute("DELETE FROM artists WHERE playlist_id = %s", (playlist_id,))
        cursor.execute("DELETE FROM tracks WHERE playlist_id = %s", (playlist_id,))

        # Удаление самого плейлиста
        cursor.execute("DELETE FROM playlists WHERE id = %s RETURNING title, user_id", (playlist_id,))
        result = cursor.fetchone()
        conn.commit()

        # Удаление связанного JSON-файла
        if result:
            title, user_id = result
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            data_folder = os.path.join(project_root, "data")
            file_name = f"{user_id}_{title.replace(' ', '_')}_tracks.json"
            file_path = os.path.join(data_folder, file_name)
            if os.path.exists(file_path):
                os.remove(file_path)

        conn.close()
        return redirect(url_for('index'))
    except Exception as e:
        return f"Ошибка при удалении плейлиста: {e}", 500


@app.route('/update_playlist/<int:playlist_id>', methods=['POST'])
def update_playlist(playlist_id):
    """Обновление плейлиста по его ID."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Получаем данные о пользователе и плейлисте
        cursor.execute("SELECT users.user_id, playlists.kind FROM playlists JOIN users ON playlists.user_id = users.id WHERE playlists.id = %s", (playlist_id,))
        result = cursor.fetchone()
        conn.close()

        if not result:
            return f"Плейлист с ID {playlist_id} не найден", 404

        user_id, kind = result
        playlist_link = f"https://music.yandex.ru/users/{user_id}/playlists/{kind}"

        # Обновляем плейлист
        parse_playlist(playlist_link)
        load_to_db()
        return redirect(url_for('index'))
    except Exception as e:
        return f"Ошибка при обновлении плейлиста: {e}", 500

@app.route('/playlist_changes/<int:playlist_id>', methods=['GET'])
@cache.memoize(timeout=300)  # кэширование на 5 минут
def playlist_changes(playlist_id):
    """Отображение истории изменений для плейлиста."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT playlists.title, playlist_versions.change_type, 
                   playlist_versions.track_title, playlist_versions.timestamp
            FROM playlist_versions
            JOIN playlists ON playlist_versions.playlist_id = playlists.id
            WHERE playlist_versions.playlist_id = %s
            ORDER BY playlist_versions.timestamp DESC
        """, (playlist_id,))
        changes = cursor.fetchall()
        conn.close()

        # Форматируем данные для шаблона
        formatted_changes = [{
            "change_type": change[1],
            "track_title": change[2],
            "timestamp": change[3].strftime('%d.%m.%y'),
        } for change in changes]
        return render_template('playlist_changes.html', playlist_title=changes[0][0], changes=formatted_changes)
    except Exception as e:
        return f"Ошибка при получении истории изменений: {e}", 500

@celery.task
def process_playlist_async(playlist_link):
    parse_playlist(playlist_link)
    load_to_db()

if __name__ == '__main__':
    app.run(debug=True)