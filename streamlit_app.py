import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube Parser Debug", page_icon="🛠️", layout="centered")

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
def send_results_to_telegram(file_data, file_name, ai_text=None):
    try:
        caption = f"📂 {file_name}"
        if ai_text: caption += "\n\n(Отчет AI внутри)"
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': caption}, 
            files={'document': (file_name, file_data)}
        )
        if ai_text:
            requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", 
                          json={'chat_id': TG_CHAT_ID, 'text': ai_text[:4000], 'parse_mode': 'Markdown'})
    except: pass

# --- ФУНКЦИЯ ДИАГНОСТИКИ (Самая важная сейчас) ---
def debug_gemini_connection():
    st.info("📡 Начинаю диагностику...")
    
    # 1. Проверяем, видит ли ключ модели вообще
    url_list = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}"
    try:
        response = requests.get(url_list)
        data = response.json()
        
        if 'error' in data:
            return f"❌ **Ошибка доступа к API:**\nCode: {data['error']['code']}\nMessage: {data['error']['message']}"
            
        models = [m['name'] for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
        st.write(f"✅ Доступно моделей: {len(models)}")
        
        if not models:
            return "⚠️ Список моделей пуст! (Возможно, гео-блокировка ключа)"

    except Exception as e:
        return f"❌ Ошибка сети при запросе списка: {e}"

    # 2. Пробуем самую надежную модель
    target_model = 'gemini-1.5-flash'
    # Если флэша нет в списке, берем первую попавшуюся
    if not any(target_model in m for m in models):
        target_model = models[0].replace('models/', '')
    
    st.write(f"🧪 Пробую тестовый запрос к `{target_model}`...")
    
    url_gen = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={GEMINI_KEY}"
    payload = {"contents": [{"parts": [{"text": "Hello, are you working?"}]}]}
    
    try:
        resp = requests.post(url_gen, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code == 200:
            return f"🎉 **УСПЕХ!** AI ответил: {resp.json()['candidates'][0]['content']['parts'][0]['text']}"
        else:
            return f"❌ **Ошибка генерации ({resp.status_code}):**\n{resp.text}"
    except Exception as e:
        return f"❌ Сбой запроса: {e}"

# --- ФУНКЦИЯ АНАЛИЗА ---
def get_ai_summary(comments_list):
    if not comments_list: return "Нет данных."
    text_corpus = "\n".join([str(c['Текст'])[:400] for c in comments_list[:80]])
    
    # Используем модель, которая должна работать
    model = 'gemini-1.5-flash'
    prompt = f"Проанализируй комментарии YouTube. Кратко: 1. Настроение. 2. Темы. 3. Вывод. Текст: {text_corpus}"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_KEY}"
    try:
        response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, headers={"Content-Type": "application/json"})
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        else:
            return f"Ошибка AI: {response.text}"
    except Exception as e:
        return f"Сбой: {e}"

# --- ПАРСИНГ ---
def process_videos(api_key, urls):
    youtube = build('youtube', 'v3', developerKey=api_key)
    all_data = []
    file_name = "comments.xlsx"
    for i, url in enumerate(urls):
        if "v=" in url: v_id = url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url: v_id = url.split("youtu.be/")[1].split("?")[0]
        else: continue
        try:
            vid_req = youtube.videos().list(part="snippet", id=v_id).execute()
            if vid_req['items']: file_name = f"{re.sub(r'[^\w\s-]', '', vid_req['items'][0]['snippet']['title'])[:30]}.xlsx"
            req = youtube.commentThreads().list(part="snippet", videoId=v_id, maxResults=100)
            while req:
                resp = req.execute()
                for item in resp['items']: all_data.append({'Автор': item['snippet']['topLevelComment']['snippet']['authorDisplayName'], 'Текст': item['snippet']['topLevelComment']['snippet']['textDisplay']})
                req = youtube.commentThreads().list_next(req, resp)
        except: pass
    return all_data, file_name

# --- ИНТЕРФЕЙС ---
st.title("YouTube Parser Debug 🛠️")

raw_urls = st.text_area("Ссылка на видео:", height=100)
use_ai = st.toggle("Подключить AI-анализ", value=False)
debug_mode = st.checkbox("Режим глубокой отладки (Показать ошибку)")

if debug_mode:
    if st.button("🔴 ТЕСТ СОЕДИНЕНИЯ С AI"):
        result = debug_gemini_connection()
        st.markdown(result)

if st.button("Начать работу", type="primary"):
    if not raw_urls: st.warning("Вставьте ссылку")
    else:
        with st.spinner('Парсим...'):
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
        
        if data:
            summary = None
            if use_ai:
                with st.spinner('AI думает...'):
                    summary = get_ai_summary(data)
                
                if "Ошибка" in summary or "Сбой" in summary:
                    st.error(summary)
                else:
                    st.success("Анализ готов!")
                    st.markdown(summary)

            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer: df.to_excel(writer, index=False)
            send_results_to_telegram(buffer.getvalue(), fname, summary)
            st.download_button("Скачать Excel", buffer.getvalue(), fname)
