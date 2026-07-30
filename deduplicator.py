import os
import json
import torch
from sentence_transformers import SentenceTransformer, util

MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'
STATE_FILE = 'sent_news_cache.json'
SIMILARITY_THRESHOLD = 0.85  # 85% смыслового совпадения считается дубликатом

# Инициализация модели (при первом запуске скачает ~80 МБ, далее использует кэш)
model = SentenceTransformer(MODEL_NAME)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_state(state):
    # Храним только последние 1000 новостей, чтобы файл не разрастался и проверка была быстрой
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state[-1000:], f, ensure_ascii=False, indent=2)

def is_semantic_duplicate(new_text, state):
    if not state:
        return False
    
    # Берем только текст для экономии памяти
    cached_texts = [item['text'] for item in state]
    
    # Кодирование нового текста и кэша
    new_embedding = model.encode(new_text, convert_to_tensor=True)
    cached_embeddings = model.encode(cached_texts, convert_to_tensor=True)
    
    # Вычисление косинусного сходства
    cosine_scores = util.cos_sim(new_embedding, cached_embeddings)[0]
    
    return bool(torch.max(cosine_scores).item() > SIMILARITY_THRESHOLD)
