import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube AI Diagnostic", page_icon="🛠️", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- ФУНКЦИЯ ДИАГНОСТИКИ МОДЕЛЕЙ ---
def find_working_model(api_key):
    """Спрашивает у Google список доступных моделей"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            # Ищем модели, умеющие генерировать текст
            available = [m['name'] for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
            return available, None
        else:
            return [], f"Ошибка API ({response.status_code}): {response.text}"
    except Exception as e:
        return [], f"Ошибка сети: {str(e)}"

# --- ФУНКЦИЯ АНАЛИЗА ---
def get_ai_summary(comments_list, model_name):
    if not comments_list: return "Нет данных."
    
    text_corpus = "\n".join([str(c['Текст'])[:400] for c in comments_list[:80]])
    prompt = f"Проанализируй эти комментарии youtube. Кратко: 1. Настроение. 2. Темы. 3. Хвалят/Ругают. Текст: {text_corpus}"
    
    # model_name уже приходит в формате 'models/gemini-...'
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={GEMINI_KEY}"
    
    try:
        response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, headers={"Content-Type": "application/json"})
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        return f"Ошибка генерации ({response.status_code}): {response.text}"
    except Exception as e:
        return f"Сбой: {e}"

# --- ФУНКЦИИ YOUTUBE (Без изменений) ---
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
            # Получаем название видео
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
st.title("YouTube Parser + Diagnostic 🛠️")

# БЛОК АВТО-ДИАГНОСТИКИ ПРИ ЗАПУСКЕ
with st.expander("📡 Статус подключения к AI", expanded=True):
    models, error = find_working_model(GEMINI_KEY)
    if error:
        st.error(f"❌ Связь с Google AI не работает: {error}")
        st.write("Совет: Проверьте, включен ли API в Google Console.")
        active_model = None
    elif not models:
        st.warning("⚠️ Google ответил, но список моделей пуст. Возможно, ограничения аккаунта.")
        active_model = None
    else:
        # Автоматически выбираем Pro или Flash, если они есть, иначе берем первую попавшуюся
        preferred = [m for m in models if 'gemini-1.5-pro' in m]
        if not preferred: preferred = [m for m in models if 'gemini-1.5-flash' in m]
        
        active_model = preferred[0] if preferred else models[0]
        st.success(f"✅ Подключено! Используем модель: **{active_model}**")
        st.caption(f"Всего доступно моделей: {len(models)}")

raw_urls = st.text_area("Ссылка на видео:", height=100)

if st.button("Запустить анализ", type="primary", disabled=(active_model is None)):
    if not raw_urls:
        st.warning("Вставьте ссылку")
    else:
        with st.spinner(f'Работает модель {active_model}...'):
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
        
        if data:
            st.subheader("📊 Результат")
            summary = get_ai_summary(data, active_model)
            
            if "Ошибка" in summary:
                st.error(summary)
            else:
                st.info(summary)
            
            # Excel
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            st.download_button("Скачать Excel", buffer.getvalue(), fname)
            
            # Отправка в Telegram (упрощенная)
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument",
                    data={'chat_id': TG_CHAT_ID, 'caption': summary[:900]},
                    files={'document': (fname, buffer.getvalue())}
                )
            except: pass
