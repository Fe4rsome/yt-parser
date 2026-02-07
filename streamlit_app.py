import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests
import time

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube Parser", page_icon="📉", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- ФУНКЦИИ СВЯЗИ ---
def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, json={'chat_id': TG_CHAT_ID, 'text': text, 'parse_mode': 'Markdown'})
    except: pass

def send_results_to_telegram(file_data, file_name, ai_text=None):
    # 1. Отправляем Файл (Всегда)
    try:
        caption = f"📂 {file_name}"
        if ai_text:
            caption += "\n\n(См. отчет следующим сообщением)"
            
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': caption}, 
            files={'document': (file_name, file_data)}
        )
    except: pass
    
    # 2. Отправляем Текст AI (Только если он есть)
    if ai_text:
        url_msg = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            if len(ai_text) > 4000:
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[:4000], 'parse_mode': 'Markdown'})
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[4000:], 'parse_mode': 'Markdown'})
            else:
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text, 'parse_mode': 'Markdown'})
        except: pass

# --- ФУНКЦИЯ АНАЛИЗА ---
def get_ai_summary_lazy(comments_list):
    if not comments_list: return "Нет данных.", None

    text_corpus = "\n".join([str(c['Текст'])[:400] for c in comments_list[:80]])
    
    prompt = f"""
    Проанализируй комментарии YouTube.
    Отчет на русском:
    1. 🎭 Настроение.
    2. 🔥 Темы споров.
    3. 👍 Позитив.
    4. 👎 Негатив.
    5. 🧠 Вывод.
    
    Текст: {text_corpus}
    """
    
    # Порядок перебора моделей
    models = ['gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-pro']
    
    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        try:
            response = requests.post(
                url, 
                json={"contents": [{"parts": [{"text": prompt}]}]}, 
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text'], model
            elif response.status_code == 429:
                continue # Лимит, пробуем следующую
        except: continue
            
    return "⚠️ Не удалось подключиться к AI (все модели заняты или ошибка сети).", None

# --- ПАРСИНГ ---
def get_video_id(url):
    if "v=" in url: return url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url: return url.split("youtu.be/")[1].split("?")[0]
    return None

def process_videos(api_key, urls):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    file_name = "comments.xlsx"
    
    for i, url in enumerate(urls):
        v_id = get_video_id(url)
        if not v_id: continue
        try:
            vid_req = youtube.videos().list(part="snippet", id=v_id).execute()
            if vid_req['items']:
                title = vid_req['items'][0]['snippet']['title']
                file_name = f"{re.sub(r'[^\w\s-]', '', title)[:30]}.xlsx"
            
            req = youtube.commentThreads().list(part="snippet", videoId=v_id, maxResults=100)
            while req:
                resp = req.execute()
                for item in resp['items']:
                    top = item['snippet']['topLevelComment']['snippet']
                    all_data.append({'Автор': top['authorDisplayName'], 'Текст': top['textDisplay']})
                req = youtube.commentThreads().list_next(req, resp)
        except: pass
    return all_data, file_name

# --- ИНТЕРФЕЙС ---
st.title("YouTube Parser 🛠️")

# 1. ПОЛЕ ДЛЯ ССЫЛКИ
raw_urls = st.text_area("Ссылка на видео:", height=100)

# 2. РУБИЛЬНИК AI (По умолчанию ВЫКЛЮЧЕН)
use_ai = st.toggle("Подключить AI-анализ (Gemini)", value=False)

# 3. КНОПКА ЗАПУСКА
if st.button("Начать работу", type="primary"):
    if not raw_urls:
        st.warning("Вставьте ссылку")
    else:
        # ЭТАП 1: Сбор (работает всегда)
        with st.spinner('Скачиваем комментарии...'):
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
        
        if data:
            summary = None
            
            # ЭТАП 2: AI (Только если включен рубильник)
            if use_ai:
                with st.spinner('Gemini анализирует...'):
                    summary, used_model = get_ai_summary_lazy(data)
                
                if used_model:
                    st.success(f"Анализ готов! (Модель: {used_model})")
                    st.markdown(summary)
                else:
                    st.warning(summary) # Вывод ошибки, если AI не смог
            else:
                st.info("AI анализ отключен. Только Excel.")

            # ЭТАП 3: Excel (работает всегда)
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            # ЭТАП 4: Отправка (AI текст отправляем, только если он есть)
            send_results_to_telegram(buffer.getvalue(), fname, summary)
            
            st.download_button("Скачать Excel", buffer.getvalue(), fname)
            
            if not use_ai:
                st.caption("✅ Файл отправлен в Telegram.")
