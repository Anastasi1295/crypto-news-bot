import os
import json
import torch
from sentence_transformers import SentenceTransformer, util

MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'
STATE_FILE = 'sent_news_cache.json'
SIMILARITY_THRESHOLD = 0.85 # Порог смыслового совпадения (85%)

# Инициализация модели (кэшируется при первом запуске)
model = SentenceTransformer(MODEL_NAME)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_state(state):
    # Ограничиваем кэш последними 500 записями для оптимизации памяти и скорости
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state[-500:], f, ensure_ascii=False, indent=2)

def is_semantic_duplicate(new_text, state):
    if not state:
        return False
    
    new_embedding = model.encode(new_text, convert_to_tensor=True)
    cached_texts = [item['text'] for item in state]
    cached_embeddings = model.encode(cached_texts, convert_to_tensor=True)
    
    # Вычисление косинусного сходства между новой новостью и всеми сохраненными
    cosine_scores = util.cos_sim(new_embedding, cached_embeddings)[0]
    
    return bool(torch.max(cosine_scores).item() > SIMILARITY_THRESHOLD)
