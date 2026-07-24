import os
import json
import re
import feedparser
import httpx
import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# === НАСТРОЙКИ ===
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
CHANNEL_ID = os.environ['CHANNEL_ID']
GROQ_API_KEY = os.environ['GROQ_API_KEY']

# Отправляем только новости, опубликованные за последние N часов
HOURS_AGO = 24

RSS_FEEDS = [
    "https://www.theblock.co/rss.xml",
    "https://www.binance.com/en/blog/rss",
    "https://blog.kraken.com/feed/",
    "https://www.coinbase.com/blog/rss.xml",
    "https://cyprus-mail.com/feed/",
]

# Ключевые слова для фильтрации
EXCHANGES = ["binance", "kraken", "okx", "bybit", "coinbase"]
STABLECOINS = ["usdt", "usdc", "tether", "circle", "stablecoin", "e-money token", "emt", "art"]
REGULATORY = [
    "mica", "caspr", "license", "authorized", "sepa", "sepainstant",
    "кипр", "cyprus", "налог", "tax", "aml", "travel rule", "bafin", "amf",
    "esma", "eba", "ecb", "dlr", "crypto-asset", "casp"
]

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===

def parse_entry_date(entry):
    """Пытается извлечь дату публикации из RSS-записи. Возвращает datetime или None."""
    # Пробуем разные поля, которые могут содержать дату
    for date_field in ['published', 'updated', 'created']:
        date_str = entry.get(date_field)
        if date_str:
            try:
                return parsedate_to_datetime(date_str)
            except Exception:
                pass
    
    # Если в entry есть published_parsed (кортеж)
    if entry.get('published_parsed'):
        try:
            from calendar import timegm
            ts = timegm(entry['published_parsed'])
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
    
    return None


def is_news_fresh(entry, hours_ago):
    """Проверяет, что новость опубликована не позже чем hours_ago часов назад."""
    pub_date = parse_entry_date(entry)
    if pub_date is None:
        # Если дату не удалось распарсить — считаем новость свежей (на всякий случай)
        return True
    
    # Убедимся, что дата с таймзоной
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return pub_date >= cutoff


def fetch_and_filter_rss():
    """Собирает RSS и фильтрует по ключевым словам + дате."""
    all_news = []
    skipped_old = 0
    skipped_irrelevant = 0
    
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        source_name = feed.feed.get('title', url)
        
        for entry in feed.entries[:30]:  # Берём побольше, чтобы не пропустить свежие
            title = entry.get('title', '')
            summary = entry.get('summary', entry.get('description', ''))
            link = entry.get('link', '')
            
            # 1. Фильтр по дате
            if not is_news_fresh(entry, HOURS_AGO):
                skipped_old += 1
                continue
            
            # 2. Фильтр по ключевым словам
            text = f"{title} {summary}".lower()
            
            has_exchange = any(ex in text for ex in EXCHANGES)
            has_stable = any(st in text for st in STABLECOINS)
            has_reg = any(reg in text for reg in REGULATORY)
            
            # Условие: (Биржа ИЛИ Стейблкоин) И (Регуляторика)
            if not ((has_exchange or has_stable) and has_reg):
                skipped_irrelevant += 1
                continue
            
            all_news.append({
                'title': title,
                'summary': summary,
                'link': link,
                'source': source_name
            })
    
    print(f"Пропущено устаревших: {skipped_old}")
    print(f"Пропущено нерелевантных: {skipped_irrelevant}")
    return all_news


async def analyze_with_groq(title, summary):
    """Анализирует новость через Groq LLM."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ты — старший аналитик по крипто-регулированию в ЕС. "
                            "Оцени новость по критериям: "
                            "1) Лицензирование бирж (Binance, Kraken, OKX, Bybit, Coinbase) по MiCA, "
                            "2) Операции с USDT/USDC (ограничения, новые пары, изменения в депозитах/выводах), "
                            "3) SEPA/банкинг (блокировки счетов, новые коридоры), "
                            "4) Налоги Кипра для криптоактивов. "
                            "Верни СТРОГО валидный JSON: "
                            "{\"relevance_score\": 1-10, \"tags\": [\"#MiCA\", \"#SEPA\", \"#CyprusTax\", \"#Stablecoin\", \"#ExchangeLicense\", \"#AMLD6\"], "
                            "\"summary_ru\": \"Суть в 2 предложениях с акцентом на последствия для трейдеров/бизнеса\", "
                            "\"action_required\": \"Да/Нет\"}"
                        )
                    },
                    {
                        "role": "user",
                        "content": f"Заголовок: {title}\nТекст: {summary[:1000]}"
                    }
                ],
                "temperature": 0.2,
                "max_tokens": 300
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            content = data['choices'][0]['message']['content']
            try:
                json_match = re.search(r'\{.*?\}', content, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group())
            except Exception as e:
                print(f"Ошибка парсинга JSON от LLM: {e}")
            return {
                "relevance_score": 5,
                "tags": ["#General"],
                "summary_ru": content,
                "action_required": "Нет"
            }
        else:
            print(f"Groq API error: {response.status_code} - {response.text[:200]}")
            return {
                "relevance_score": 0,
                "tags": ["#Error"],
                "summary_ru": f"Ошибка анализа: {response.status_code}",
                "action_required": "Нет"
            }


async def send_to_telegram(analysis, title, link, source):
    """Отправляет отформатированное сообщение в Telegram."""
    tags_str = " ".join(analysis.get("tags", ["#Crypto"]))
    action_emoji = " ⚠️ ТРЕБУЕТ ВНИМАНИЯ" if str(analysis.get("action_required", "Нет")).lower() == "да" else ""
    relevance = analysis.get("relevance_score", "N/A")
    
    text = (
        f"{tags_str}{action_emoji}\n\n"
        f"<b>{title}</b>\n\n"
        f"{analysis.get('summary_ru', 'Нет описания')}\n\n"
        f"📰 Источник: {source}\n"
        f"🔗 [Читать оригинал]({link})\n\n"
        f"Оценка важности: {relevance}/10"
    )
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHANNEL_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
        )
        return response.status_code == 200


async def main():
    print("=" * 50)
    print("=== Запуск крипто-аналитика ===")
    print(f"Фильтр: новости за последние {HOURS_AGO} часов")
    print("=" * 50)
    
    # Собираем и фильтруем новости
    all_news = fetch_and_filter_rss()
    print(f"\nНайдено {len(all_news)} релевантных свежих новостей")
    
    if not all_news:
        print("Новостей для отправки нет. Завершаем работу.")
        return
    
    # Обрабатываем максимум 5 новостей за запуск
    sent_count = 0
    skipped_count = 0
    
    for news in all_news[:5]:
        print(f"\n--- Анализ: {news['title'][:60]}... ---")
        
        analysis = await analyze_with_groq(news['title'], news['summary'])
        relevance = analysis.get("relevance_score", 0)
        print(f"Оценка важности: {relevance}/10")
        
        # Публикуем только если оценка >= 6
        if relevance >= 6:
            success = await send_to_telegram(
                analysis,
                news['title'],
                news['link'],
                news['source']
            )
            if success:
                print("✅ Отправлено в Telegram")
                sent_count += 1
            else:
                print(" Ошибка отправки в Telegram")
        else:
            print(f"⏭️ Пропущено (низкая важность)")
            skipped_count += 1
    
    print("\n" + "=" * 50)
    print(f"=== Готово! Отправлено: {sent_count}, пропущено: {skipped_count} ===")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
