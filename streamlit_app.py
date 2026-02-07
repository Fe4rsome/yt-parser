import streamlit as st
import pandas as pd
from googleapiclient.discovery import build
import io
import re
import requests

# --- НАСТРОЙКИ ---
st.set_page_config(page_title="YouTubeComm", page_icon="📉", layout="centered")

# --- СЕКРЕТЫ ---
try:
    API_KEY = st.secrets["GOOGLE_API_KEY"]
    TG_TOKEN = st.secrets["TELEGRAM_TOKEN"]
    TG_CHAT_ID = st.secrets["TELEGRAM_CHAT_ID"]
    GEMINI_KEY = st.secrets["GEMINI_API_KEY"]
except Exception as e:
    st.error(f"Ошибка Secrets: {e}")
    st.stop()

# --- ИНИЦИАЛИЗАЦИЯ ПАМЯТИ (SESSION STATE) ---
# Это "жесткий диск" вашего приложения. Данные здесь не исчезают при обновлении.
if 'data_processed' not in st.session_state:
    st.session_state['data_processed'] = False
if 'excel_buffer' not in st.session_state:
    st.session_state['excel_buffer'] = None
if 'file_name' not in st.session_state:
    st.session_state['file_name'] = ""
if 'ai_summary' not in st.session_state:
    st.session_state['ai_summary'] = None
if 'model_name' not in st.session_state:
    st.session_state['model_name'] = None

# --- ТЕЛЕГРАМ ---
def send_results_to_telegram(file_data, file_name, ai_text=None):
    try:
        # 1. Документ
        caption = f"📂 {file_name}"
        if ai_text: caption += "\n\n(Отчет AI ниже)"
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument", 
            data={'chat_id': TG_CHAT_ID, 'caption': caption}, 
            files={'document': (file_name, file_data)}
        )
        # 2. Текст (если есть)
        if ai_text:
            url_msg = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            if len(ai_text) > 4000:
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[:4000], 'parse_mode': 'Markdown'})
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text[4000:], 'parse_mode': 'Markdown'})
            else:
                requests.post(url_msg, json={'chat_id': TG_CHAT_ID, 'text': ai_text, 'parse_mode': 'Markdown'})
    except: pass

# --- AI АНАЛИЗ ---
def get_ai_summary(comments_list):
    if not comments_list: return "Нет данных.", None
    
    text_corpus = "\n".join([str(c['Текст'])[:400] for c in comments_list[:80]])
    
    prompt = f"""
    Проанализируй комментарии YouTube.
    Напиши отчет на русском языке. Используй форматирование Markdown.
    Структура:
    1. 🎭 Настроение
    2. 🔥 Темы споров
    3. 👍 Позитив
    4. 👎 Негатив
    5. 🧠 Вывод
    
    Текст: {text_corpus}
    """
    
    # Приоритет на 2.5, так как она проверена
    models = ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-1.5-flash']
    
    last_error = ""
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
            else:
                last_error = f"{model}: {response.status_code}"
                continue
        except Exception as e:
            last_error = str(e)
            continue
            
    return f"⚠️ Ошибка AI: {last_error}", None

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
            if vid_req['items']: 
                title = vid_req['items'][0]['snippet']['title']
                file_name = f"{re.sub(r'[^\w\s-]', '', title)[:30]}.xlsx"
            
            req = youtube.commentThreads().list(part="snippet", videoId=v_id, maxResults=100)
            while req:
                resp = req.execute()
                for item in resp['items']: 
                    all_data.append({
                        'Автор': item['snippet']['topLevelComment']['snippet']['authorDisplayName'], 
                        'Текст': item['snippet']['topLevelComment']['snippet']['textDisplay']
                    })
                req = youtube.commentThreads().list_next(req, resp)
        except: pass
    return all_data, file_name

# --- ИНТЕРФЕЙС ---

# 1. Стиль заголовка (по центру, уменьшенный)
st.markdown("<h3 style='text-align: center; margin-bottom: 20px;'>YouTubeComm</h3>", unsafe_allow_html=True)

# 2. Ввод данных
raw_urls = st.text_area("Ссылка на видео:", height=100, placeholder="Вставьте ссылку сюда...")
use_ai = st.toggle("Подключить AI-анализ", value=False)

# 3. КНОПКА ЗАПУСКА
# Мы используем callback или просто проверку нажатия.
if st.button("Начать работу", type="primary", use_container_width=True):
    if not raw_urls:
        st.warning("Вставьте ссылку")
    else:
        # СБРАСЫВАЕМ СТАРЫЕ ДАННЫЕ
        st.session_state['data_processed'] = False
        st.session_state['ai_summary'] = None
        
        with st.spinner('Обработка...'):
            # А. Скачиваем данные
            data, fname = process_videos(API_KEY, raw_urls.split('\n'))
            
            if data:
                # Б. Готовим Excel
                df = pd.DataFrame(data)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False)
                
                # В. Анализ AI
                summary_text = None
                mod_name = None
                
                if use_ai:
                    summary_text, mod_name = get_ai_summary(data)
                
                # Г. СОХРАНЯЕМ ВСЕ В ПАМЯТЬ (ВАЖНО!)
                st.session_state['excel_buffer'] = buffer.getvalue()
                st.session_state['file_name'] = fname
                st.session_state['ai_summary'] = summary_text
                st.session_state['model_name'] = mod_name
                st.session_state['data_processed'] = True # Флаг успеха
                
                # Д. Отправляем в телеграм (один раз)
                send_results_to_telegram(buffer.getvalue(), fname, summary_text)
                
            else:
                st.error("Не удалось собрать комментарии (проверьте ссылку или доступ).")

# --- БЛОК РЕЗУЛЬТАТОВ (ОТОБРАЖАЕТСЯ ВСЕГДА, ЕСЛИ ЕСТЬ ДАННЫЕ) ---
# Этот блок находится ВНЕ кнопки. Он не исчезнет при обновлении.

if st.session_state['data_processed']:
    st.divider() # Разделительная линия
    
    # 1. Показываем результат AI
    if st.session_state['ai_summary']:
        if st.session_state['model_name']:
            st.success(f"Анализ готов ({st.session_state['model_name']})")
            st.markdown(st.session_state['ai_summary'])
        else:
            st.error(st.session_state['ai_summary'])
    elif use_ai:
        pass # Если AI был включен, но результат пустой (ошибка выше)
    else:
        st.info("Таблица готова (без AI).")

    # 2. Кнопка скачивания (ТЕПЕРЬ ОНА СТАБИЛЬНАЯ)
    st.download_button(
        label=f"📥 Скачать {st.session_state['file_name']}",
        data=st.session_state['excel_buffer'],
        file_name=st.session_state['file_name'],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="secondary",
        use_container_width=True
    )
