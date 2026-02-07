import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests  # Добавили для отправки в Telegram

# --- НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="YouTube Parser", page_icon="🔴", layout="centered")

# --- ПОЛУЧЕНИЕ СЕКРЕТОВ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
except:
    st.error("Настройте Secrets (Ключи API) в панели управления Streamlit!")
    st.stop()

# --- ФУНКЦИЯ ОТПРАВКИ В TELEGRAM ---
def send_to_telegram(file_data, file_name):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    files = {'document': (file_name, file_data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
    data = {'chat_id': TG_CHAT_ID, 'caption': f"✅ Файл готов: {file_name}"}
    try:
        requests.post(url, data=data, files=files)
        return True
    except:
        return False

# --- ВАША ИНЖЕНЕРНАЯ ЛОГИКА (БЕЗ ИЗМЕНЕНИЙ) ---
def get_video_id(url):
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
    file_name = "comments.xlsx"
    
    for i, url in enumerate(urls):
        if not url.strip(): continue
        v_id = get_video_id(url)
        if not v_id:
            logs.append(f"⚠️ Ссылка {i+1} некорректна.")
            continue
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
                    all_data.append({
                        'ID Видео': v_id, 'Тип': 'Комментарий', 'Автор': top['authorDisplayName'],
                        'Текст': top['textDisplay'], 'Лайков': top['likeCount'], 'Дата': top['publishedAt']
                    })
                    counter += 1
                    if 'replies' in item:
                        for reply in item['replies']['comments']:
                            r = reply['snippet']
                            all_data.append({
                                'ID Видео': v_id, 'Тип': 'Ответ', 'Автор': r['authorDisplayName'],
                                'Текст': r['textDisplay'], 'Лайков': r['likeCount'], 'Дата': r['publishedAt']
                            })
                            counter += 1
                req = youtube.commentThreads().list_next(req, resp)
            logs.append(f"✅ Собрано {counter} записей.")
        except Exception as e:
            logs.append(f"❌ Ошибка: {str(e)}")
    return all_data, logs, file_name

# --- ИНТЕРФЕЙС ---
st.title("YouTube Comment Downloader 🚀")
raw_urls = st.text_area("Ссылки (каждая с новой строки)", height=150)

if st.button("Начать сбор", type="primary"):
    if not raw_urls.strip():
        st.warning("Вставьте ссылки.")
    else:
        with st.spinner('Работаю...'):
            urls = raw_urls.split('\n')
            data, logs, fname = process_videos(API_KEY, urls)
        
        with st.expander("Журнал работы"):
            for log in logs: st.write(log)
        
        if data:
            df = pd.DataFrame(data)
            df['Текст'] = df['Текст'].astype(str).str.replace(r'<[^>]*>', ' ', regex=True)
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            excel_data = buffer.getvalue()
            
            # ОТПРАВКА В TELEGRAM (Новая фишка)
            if send_to_telegram(excel_data, fname):
                st.info("📂 Копия файла отправлена вам в Telegram!")
            else:
                st.warning("⚠️ Не удалось отправить копию в Telegram.")
            
            st.success(f"Готово! Собрано {len(data)} комментариев.")
            st.download_button(label=f"📥 Скачать {fname}", data=excel_data, file_name=fname)
