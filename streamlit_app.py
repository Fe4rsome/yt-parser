import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests
import google.generativeai as genai

# --- НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="YouTube AI Parser", page_icon="🧠", layout="centered")

# --- ПОЛУЧЕНИЕ СЕКРЕТОВ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка в Secrets: {e}")
    st.stop()

# --- НАСТРОЙКА GEMINI ---
genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')

# --- ФУНКЦИИ ---

def get_ai_summary(comments_list):
    """Генерация сводки через Gemini"""
    text_corpus = "\n".join([c['Текст'] for c in comments_list[:60]]) # Берем первые 60 для точности
    prompt = f"Проанализируй комментарии к видео и напиши кратко: 1. Общее настроение. 2. Основные темы. 3. Что хвалят/ругают. 4. Есть ли спам. Текст:\n{text_corpus}"
    try:
        response = ai_model.generate_content(prompt)
        return response.text
    except:
        return "Не удалось сгенерировать AI-сводку."

def send_to_telegram(file_data, file_name, ai_text):
    """Отправка файла и сводки в Telegram"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    caption = f"📊 **AI Анализ:**\n{ai_text[:900]}" # Ограничение длины подписи
    files = {'document': (file_name, file_data)}
    try:
        requests.post(url, data={'chat_id': TG_CHAT_ID, 'caption': caption, 'parse_mode': 'Markdown'}, files=files)
        return True
    except:
        return False

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
        v_id = get_video_id(url)
        if not v_id: continue
        if i == 0:
            title = get_video_title(youtube, v_id)
            file_name = f"{re.sub(r'[\\/*? Glad:<>|]', '', title)[:50]}.xlsx"
        
        try:
            req = youtube.commentThreads().list(part="snippet,replies", videoId=v_id, maxResults=100)
            while req:
                resp = req.execute()
                for item in resp['items']:
                    top = item['snippet']['topLevelComment']['snippet']
                    all_data.append({'Автор': top['authorDisplayName'], 'Текст': top['textDisplay'], 'Дата': top['publishedAt']})
                req = youtube.commentThreads().list_next(req, resp)
        except Exception as e:
            logs.append(f"Ошибка: {e}")
    return all_data, logs, file_name

# --- ИНТЕРФЕЙС ---
st.title("YouTube AI Parser 🚀")
raw_urls = st.text_area("Вставьте ссылки (каждая с новой строки)", height=150)

if st.button("Начать сбор и AI-анализ", type="primary"):
    if not raw_urls.strip():
        st.warning("Введите ссылки!")
    else:
        with st.spinner('Анализирую...'):
            urls = raw_urls.split('\n')
            data, logs, fname = process_videos(API_KEY, urls)
        
        if data:
            # Вывод AI сводки
            st.subheader("🤖 Сводка от Gemini AI")
            ai_summary = get_ai_summary(data)
            st.info(ai_summary)
            
            # Подготовка файла
            df = pd.DataFrame(data)
            df['Текст'] = df['Текст'].astype(str).str.replace(r'<[^>]*>', ' ', regex=True)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            # Отправка в ТГ и кнопка
            send_to_telegram(buffer.getvalue(), fname, ai_summary)
            st.success("Данные собраны и отправлены в Telegram!")
            st.download_button(f"📥 Скачать {fname}", buffer.getvalue(), fname)

