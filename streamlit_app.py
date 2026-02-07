import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube Pro Parser", page_icon="🇺🇸", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"] # Сюда вставьте ключ от US аккаунта
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- ФУНКЦИЯ AI (С поддержкой Pro модели) ---
def get_ai_summary(comments_list):
    if not comments_list: return "Нет данных."

    # Для Pro модели можно взять больше контекста (до 100 комментариев)
    text_corpus = "\n".join([str(c['Текст'])[:500] for c in comments_list[:100]])
    
    prompt = f"""
    Ты опытный аналитик. Проанализируй комментарии к видео.
    Составь структурированный отчет на русском языке:
    1. 🎭 **Эмоциональный фон:** (Позитив/Негатив/Сарказм).
    2. 🔥 **Главные темы обсуждения:** (О чем спорят).
    3. 👍 **Что хвалят:** (Конкретные фичи/моменты).
    4. 👎 **Что ругают:** (Баги/Проблемы/Цену).
    5. 💡 **Инсайт:** Самый интересный или необычный комментарий.
    
    Текст комментариев:
    {text_corpus}
    """
    
    # Сначала пробуем самую мощную модель (Pro), затем быструю (Flash)
    models = ["gemini-1.5-pro", "gemini-1.5-flash"]

    for model in models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
        headers = {"Content-Type": "application/json"}
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                return response.json()['candidates'][0]['content']['parts'][0]['text']
        except:
            continue
            
    return "Не удалось получить ответ (проверьте VPN или ключ)."

# --- ОСТАЛЬНЫЕ ФУНКЦИИ (БЕЗ ИЗМЕНЕНИЙ) ---
def send_to_telegram(file_data, file_name, ai_text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    # Обрезаем до 1000 символов для подписи
    caption = f"🇺🇸 **Pro Анализ:**\n\n{ai_text[:950]}" 
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
st.title("YouTube Pro Parser 🇺🇸")
raw_urls = st.text_area("Вставьте ссылки", height=150)

if st.button("Анализировать (Pro)", type="primary"):
    if not raw_urls.strip():
        st.warning("Нет ссылок")
    else:
        with st.spinner('Gemini Pro думает...'):
            urls = raw_urls.split('\n')
            data, logs, fname = process_videos(API_KEY, urls)
        
        if data:
            st.subheader("📊 Аналитика")
            summary = get_ai_summary(data)
            st.markdown(summary)
            
            df = pd.DataFrame(data)
            df['Текст'] = df['Текст'].astype(str).str.replace(r'<[^>]*>', ' ', regex=True)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            send_to_telegram(buffer.getvalue(), fname, summary)
            st.success("Готово!")
            st.download_button(f"Скачать {fname}", buffer.getvalue(), fname)
