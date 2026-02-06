import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re

# --- НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="YouTube Parser", page_icon="🔴", layout="centered")

# --- ПОЛУЧЕНИЕ КЛЮЧА ИЗ СЕКРЕТОВ (ДЛЯ ОБЛАКА) ---
# Если запускаем локально, ищем в st.secrets, иначе просим ввести
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
except:
    # Если секретов нет (например, первый запуск локально), покажем поле ввода
    API_KEY = st.text_input("Введите Google API Key", type="password")

# --- ФУНКЦИИ (ВАШ ИНЖЕНЕРНЫЙ БЭКЕНД) ---

def get_video_id(url):
    """Вытаскиваем ID видео из любой ссылки"""
    url = url.strip()
    if "v=" in url: return url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url: return url.split("youtu.be/")[1].split("?")[0]
    return url if len(url) == 11 else None

def get_video_title(youtube, video_id):
    try:
        resp = youtube.videos().list(part="snippet", id=video_id).execute()
        return resp['items'][0]['snippet']['title']
    except:
        return f"Video_{video_id}"

def process_videos(api_key, urls):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    logs = []
    
    # Имя первого файла для названия Excel
    file_name = "comments.xlsx"
    
    for i, url in enumerate(urls):
        if not url.strip(): continue
        
        v_id = get_video_id(url)
        if not v_id:
            logs.append(f"⚠️ Ссылка {i+1} некорректна, пропускаем.")
            continue
            
        # Получаем название (если это первое видео)
        if i == 0:
            title = get_video_title(youtube, v_id)
            clean_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
            file_name = f"{clean_title}.xlsx"
        
        logs.append(f"🔍 Скачиваю: {v_id}...")
        
        try:
            req = youtube.commentThreads().list(
                part="snippet,replies", videoId=v_id, maxResults=100, order="time"
            )
            
            counter = 0
            while req:
                resp = req.execute()
                for item in resp['items']:
                    top = item['snippet']['topLevelComment']['snippet']
                    
                    # Собираем данные (Анти-спам убран по просьбе)
                    all_data.append({
                        'ID Видео': v_id,
                        'Тип': 'Комментарий',
                        'Автор': top['authorDisplayName'],
                        'Текст': top['textDisplay'],
                        'Лайков': top['likeCount'],
                        'Дата': top['publishedAt']
                    })
                    counter += 1
                    
                    if 'replies' in item:
                        for reply in item['replies']['comments']:
                            r = reply['snippet']
                            all_data.append({
                                'ID Видео': v_id,
                                'Тип': 'Ответ',
                                'Автор': r['authorDisplayName'],
                                'Текст': r['textDisplay'],
                                'Лайков': r['likeCount'],
                                'Дата': r['publishedAt']
                            })
                            counter += 1
                
                req = youtube.commentThreads().list_next(req, resp)
            
            logs.append(f"✅ Успешно! Собрано {counter} записей с этого видео.")
            
        except Exception as e:
            logs.append(f"❌ Ошибка с {v_id}: {str(e)}")

    return all_data, logs, file_name

# --- ИНТЕРФЕЙС (FRONTEND) ---

st.title("YouTube Comment Downloader 🚀")
st.write("Вставьте ссылки на видео (каждую с новой строки):")

# Поле для ввода ссылок
raw_urls = st.text_area("Ссылки", height=150, placeholder="https://www.youtube.com/watch?v=...")

if st.button("Начать сбор", type="primary"):
    if not API_KEY:
        st.error("Ошибка: Не указан API Key.")
    elif not raw_urls.strip():
        st.warning("Пожалуйста, вставьте хотя бы одну ссылку.")
    else:
        # Показываем спиннер загрузки
        with st.spinner('Работаю... Это может занять время...'):
            urls = raw_urls.split('\n')
            data, logs, fname = process_videos(API_KEY, urls)
        
        # Выводим логи
        with st.expander("Журнал работы (Логи)"):
            for log in logs:
                st.write(log)
        
        # Если есть данные — даем скачать
        if data:
            df = pd.DataFrame(data)
            # Чистка тегов HTML
            df['Текст'] = df['Текст'].astype(str).str.replace(r'<[^>]*>', ' ', regex=True)
            
            # Конвертация в Excel в памяти (без сохранения на диск)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            st.success(f"Готово! Собрано {len(data)} комментариев.")
            
            # КНОПКА СКАЧИВАНИЯ
            st.download_button(
                label=f"📥 Скачать {fname}",
                data=buffer.getvalue(),
                file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.warning("Ничего не найдено.")