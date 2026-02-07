import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests
import time

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube AI Analyst", page_icon="🇺🇸", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- ФУНКЦИЯ ПРОВЕРКИ МОДЕЛИ (ТЕСТ-ДРАЙВ) ---
def test_model(api_key, model_name):
    """Пробует отправить 'Hello' модели. Возвращает True, если работает."""
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={api_key}"
    try:
        response = requests.post(
            url, 
            json={"contents": [{"parts": [{"text": "Hello"}]}]}, 
            headers={"Content-Type": "application/json"}
        )
        if response.status_code == 200:
            return True
        return False
    except:
        return False

# --- УМНЫЙ ПОИСК МОДЕЛИ ---
def find_working_model(api_key):
    """Ищет лучшую РАБОЧУЮ модель (проверяет квоты)"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            return None, f"Ошибка API: {response.status_code}"
            
        data = response.json()
        all_models = [m['name'] for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
        
        if not all_models: return None, "Список моделей пуст"

        # ПРИОРИТЕТЫ: Проверяем от самых крутых к простым
        # Мы убрали gemini-3 из топа, так как на него часто квота 0, но оставили gemini-2.0
        priorities = [
            'gemini-2.0-flash', 
            'gemini-1.5-pro', 
            'gemini-1.5-flash'
        ]
        
        # 1. Сначала ищем по приоритетам и ТЕСТИРУЕМ
        for keyword in priorities:
            # Находим все версии модели (например, gemini-1.5-pro-latest, gemini-1.5-pro-001)
            candidates = [m for m in all_models if keyword in m]
            
            for model in candidates:
                # ВАЖНО: Делаем реальный тест перед выбором!
                if test_model(api_key, model):
                    return model, None
        
        # 2. Если ничего из топа не заработало, берем любую рабочую из списка
        for model in all_models:
             if "gemini" in model and test_model(api_key, model):
                 return model, None

        return None, "Ни одна модель не прошла тест (квоты исчерпаны?)"
        
    except Exception as e:
        return None, f"Ошибка сети: {e}"

# --- ФУНКЦИЯ АНАЛИЗА ---
def get_ai_summary(comments_list, model_name):
    if not comments_list: return "Нет данных."
    
    text_corpus = "\n".join([str(c['Текст'])[:500] for c in comments_list[:80]])
    
    prompt = f"""
    Ты профессиональный аналитик. Проанализируй комментарии YouTube.
    Составь подробный отчет на русском языке:
    
    1. 🎭 **Настроение:** (Эмоции, сарказм, агрессия).
    2. 🔥 **О чем спорят:** (Главные темы).
    3. 👍 **Позитив:** (Что хвалят).
    4. 👎 **Негатив:** (Что ругают).
    5. 🧠 **Вывод:** (Краткий итог).
    
    Текст: {text_corpus}
    """
    
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={GEMINI_KEY}"
    
    try:
        response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, headers={"Content-Type": "application/json"})
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        elif response.status_code == 429:
            return "⚠️ Превышен лимит запросов (Quota Exceeded). Попробуйте через минуту."
        return f"Ошибка генерации ({response.status_code}): {response.text}"
    except Exception as e:
        return f"Сбой: {e}"

# --- ОТПРАВКА В TELEGRAM ---
def send_full_report_to_telegram(file_data, file_name, ai_text):
    # Файл
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': f"📂 Отчет: {file_name}"}, 
            files={'document': (file_name, file_data)}
        )
    except: pass
    
    # Текст (разбиваем, если длинный)
    url_msg = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        # Если текст слишком длинный для одного сообщения Telegram
        if len(ai_text) > 4000:
            part1 = ai_text[:4000]
            part2 = ai_text[4000:]
            requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': part1, 'parse_mode': 'Markdown'})
            requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': part2, 'parse_mode': 'Markdown'})
        else:
            requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text, 'parse_mode': 'Markdown'})
    except: pass

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
st.title("YouTube AI Analyst 🇺🇸")

# ДИАГНОСТИКА С ТЕСТОМ
with st.expander("📡 Поиск рабочей модели...", expanded=True):
    with st.spinner("Тестируем модели на квоты..."):
        active_model, error = find_working_model(GEMINI_KEY)
    
    if active_model:
        st.success(f"✅ Найдена рабочая модель: **{active_model}**")
        st.caption("Модели с ошибкой 429 были пропущены.")
    else:
        st.error(f"❌ Не удалось найти рабочую модель: {error}")

raw_urls = st.text_area("Ссылка на видео:", height=100)

if st.button("Анализировать", type="primary", disabled=(not active_model)):
    if not raw_urls:
        st.warning("Вставьте ссылку")
    else:
        with st.spinner('Анализирую...'):
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
        
        if data:
            st.subheader("📝 Результат")
            summary = get_ai_summary(data, active_model)
            st.markdown(summary)
            
            # Excel
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            # Отправка
            send_full_report_to_telegram(buffer.getvalue(), fname, summary)
            st.success("✅ Все отправлено в Telegram!")
            st.download_button("Скачать Excel", buffer.getvalue(), fname)
