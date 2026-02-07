import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests # Используем стандартную библиотеку запросов
# import google.generativeai - БОЛЬШЕ НЕ НУЖНО

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

# --- ФУНКЦИИ ---

def get_ai_summary(comments_list):
    """Прямой запрос к Gemini через HTTP (работает всегда)"""
    if not comments_list:
        return "Нет данных для анализа."

    # Подготовка текста (берем первые 50 комментов)
    text_corpus = "\n".join([str(c['Текст'])[:300] for c in comments_list[:50]])
    
    prompt = f"""
    Проанализируй эти комментарии к видео и напиши кратко:
    1. Общее настроение.
    2. Основные темы.
    3. Что хвалят/ругают.
    
    Текст:
    {text_corpus}
    """
    
    # ПРЯМОЙ ЗАПРОС К API (МИНУЯ БИБЛИОТЕКУ)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 200:
            # Парсим ответ JSON вручную
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            # Если ошибка - выводим точный текст от Google (поможет понять причину)
            return f"Ошибка Google API ({response.status_code}): {response.text}"
            
    except Exception as e:
        return f"Сбой соединения: {e}"

def send_to_telegram(file_data, file_name, ai_text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    caption = f"📊 **AI Анализ:**\n{ai_text[:900]}"
    files = {'document': (file_name, file_data)}
    try:
        requests.post(url, data={'chat_id': TG_CHAT_ID, 'caption': caption, 'parse_mode': 'Markdown'}, files=files)
        return True
    except:
        return False

# --- СТАНДАРТНЫЕ ФУНКЦИИ YOUTUBE ---
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
            file_name = f"{re.sub(r'[\\/*?<>|]', '', title)[:50]}.xlsx"
        
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
            st.subheader("🤖 Сводка от Gemini AI")
            ai_summary = get_ai_summary(data)
            
            # Если вернулась ошибка 400/403, показываем её красным
            if "Ошибка Google API" in ai_summary:
                st.error(ai_summary)
            else:
                st.info(ai_summary)
            
            df = pd.DataFrame(data)
            df['Текст'] = df['Текст'].astype(str).str.replace(r'<[^>]*>', ' ', regex=True)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            send_to_telegram(buffer.getvalue(), fname, ai_summary)
            st.success("Готово! Проверьте Telegram.")
            st.download_button(f"📥 Скачать {fname}", buffer.getvalue(), fname)
