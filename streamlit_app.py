import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTube AI Analyst", page_icon="🚀", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- УМНЫЙ ПОИСК МОДЕЛИ ---
def find_best_model(api_key):
    """Ищет самую новую доступную модель (Gemini 3 -> 2 -> 1.5 Pro)"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        response = requests.get(url)
        if response.status_code != 200:
            return None, f"Ошибка API: {response.status_code}"
            
        data = response.json()
        # Фильтруем только те, что умеют генерировать текст
        all_models = [m['name'] for m in data.get('models', []) if 'generateContent' in m.get('supportedGenerationMethods', [])]
        
        if not all_models: return None, "Список моделей пуст"

        # ПРИОРИТЕТЫ (Сначала ищем самые мощные)
        # Если выйдет Gemini 3, он подхватится первым
        priority_keywords = ['gemini-3', 'gemini-2', 'gemini-1.5-pro', 'flash']
        
        for keyword in priority_keywords:
            # Ищем модель, в названии которой есть ключевое слово
            found = next((m for m in all_models if keyword in m), None)
            if found:
                return found, None # Возвращаем лучшую найденную
        
        # Если ничего из приоритетов не нашли, берем первую попавшуюся
        return all_models[0], None
        
    except Exception as e:
        return None, f"Ошибка сети: {e}"

# --- ФУНКЦИЯ АНАЛИЗА ---
def get_ai_summary(comments_list, model_name):
    if not comments_list: return "Нет данных."
    
    # Берем больше контекста для мощных моделей
    text_corpus = "\n".join([str(c['Текст'])[:500] for c in comments_list[:80]])
    
    prompt = f"""
    Ты профессиональный аналитик медиа. Проанализируй комментарии.
    Дай развернутый отчет на русском языке (используй Markdown):
    
    1. 🎭 **Эмоциональный климат:** Детальное описание настроения.
    2. 🔥 **Острые темы:** О чем самые жаркие споры?
    3. 👍 **Позитив:** Что именно хвалят (цитаты/факты).
    4. 👎 **Негатив:** Конкретные претензии.
    5. 🧠 **Вывод:** Стоит ли автору что-то менять?
    
    Текст: {text_corpus}
    """
    
    # model_name уже содержит "models/..."
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={GEMINI_KEY}"
    
    try:
        response = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, headers={"Content-Type": "application/json"})
        if response.status_code == 200:
            return response.json()['candidates'][0]['content']['parts'][0]['text']
        return f"Ошибка генерации ({response.status_code}): {response.text}"
    except Exception as e:
        return f"Сбой: {e}"

# --- ОТПРАВКА В TELEGRAM (ИСПРАВЛЕННАЯ) ---
def send_full_report_to_telegram(file_data, file_name, ai_text):
    # 1. Сначала отправляем ФАЙЛ
    url_doc = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    try:
        requests.post(
            url_doc, 
            data={'chat_id': TG_CHAT_ID, 'caption': f"📂 Данные: {file_name}"}, 
            files={'document': (file_name, file_data)}
        )
    except: pass
    
    # 2. Затем отправляем ТЕКСТ (отдельным сообщением, чтобы влезло всё)
    url_msg = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        # Разбиваем на части, если вдруг текст больше 4000 символов (редко, но бывает)
        if len(ai_text) > 4000:
            ai_text = ai_text[:4000] + "\n...(обрезано Telegram)..."
            
        requests.post(
            url_msg, 
            json={'chat_id': TG_CHAT_ID, 'text': ai_text, 'parse_mode': 'Markdown'}
        )
        return True
    except: return False

# --- ПАРСИНГ YOUTUBE ---
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
st.title("YouTube AI Analyst 3.0 🚀")

# ДИАГНОСТИКА
with st.expander("🔌 Статус подключения", expanded=True):
    active_model, error = find_best_model(GEMINI_KEY)
    if active_model:
        st.success(f"✅ Используем мощнейшую доступную модель: **{active_model}**")
    else:
        st.error(f"❌ Ошибка: {error}")

raw_urls = st.text_area("Ссылка на видео:", height=100)

if st.button("Анализировать", type="primary", disabled=(not active_model)):
    if not raw_urls:
        st.warning("Вставьте ссылку")
    else:
        with st.spinner('Читаю комментарии и думаю...'):
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
        
        if data:
            st.subheader("📝 Результат анализа")
            summary = get_ai_summary(data, active_model)
            st.markdown(summary)
            
            # Excel
            df = pd.DataFrame(data)
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False)
            
            # Отправка
            send_full_report_to_telegram(buffer.getvalue(), fname, summary)
            st.success("✅ Отчет и файл отправлены в Telegram!")
            st.download_button("Скачать Excel", buffer.getvalue(), fname)
