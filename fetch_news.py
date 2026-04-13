import os
  import json
  import httpx
  import feedparser
  import re
  from datetime import datetime

  DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
  DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

  RSS_FEEDS = [
      {"source": "BBC中文", "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"},
      {"source": "联合早报", "url": "https://www.zaobao.com.sg/rss/realtime/world"},
      {"source": "联合早报", "url": "https://www.zaobao.com.sg/rss/realtime/china"},
      {"source": "中央社", "url": "https://www.cna.com.tw/rss/aall.aspx"},
      {"source": "CNN", "url": "http://rss.cnn.com/rss/edition.rss"},
      {"source": "CNN", "url": "http://rss.cnn.com/rss/edition_world.rss"},
      {"source": "CNN", "url": "http://rss.cnn.com/rss/edition_technology.rss"},
      {"source": "路透社", "url": "https://feeds.reuters.com/reuters/topNews"},
      {"source": "路透社", "url": "https://feeds.reuters.com/reuters/technologyNews"},
      {"source": "路透社", "url": "https://feeds.reuters.com/reuters/businessNews"},
  ]

  CATEGORIES = ["时政", "军事", "经济", "互联网", "AI", "新能源汽车", "3C数码", "中国特大新闻", "其他"]

  def extract_image(entry):
      if hasattr(entry, 'media_content') and entry.media_content:
          for m in entry.media_content:
              if m.get('type', '').startswith('image'):
                  return m.get('url')
      if hasattr(entry, 'enclosures') and entry.enclosures:
          for e in entry.enclosures:
              if e.get('type', '').startswith('image'):
                  return e.get('href') or e.get('url')
      summary = getattr(entry, 'summary', '') or ''
      match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary)
      if match:
          return match.group(1)
      return None

  def fetch_all_feeds():
      articles = []
      for feed_info in RSS_FEEDS:
          try:
              feed = feedparser.parse(feed_info["url"])
              for entry in feed.entries[:10]:
                  url = entry.get('link', '')
                  if not url:
                      continue
                  title = entry.get('title', '').strip()
                  if not title:
                      continue

                  published = entry.get('published_parsed') or entry.get('updated_parsed')
                  pub_time = datetime(*published[:6]).isoformat() if published else datetime.now().isoformat()

                  articles.append({
                      'source': feed_info['source'],
                      'title_original': title,
                      'title_zh': '',
                      'summary': entry.get('summary', '')[:200],
                      'url': url,
                      'image_url': extract_image(entry),
                      'category': '',
                      'published_at': pub_time,
                  })
          except Exception as e:
              print(f"Error fetching {feed_info['url']}: {e}")

      return articles

  def classify_and_translate(articles):
      if not articles or not DEEPSEEK_API_KEY:
          return articles

      batch_size = 20
      results = []

      for i in range(0, len(articles), batch_size):
          batch = articles[i:i+batch_size]
          items_text = "\n".join(
              f"{j+1}. [{a['source']}] {a['title_original']}"
              for j, a in enumerate(batch)
          )

          prompt = f"""以下是一批新闻标题，请对每条新闻：
  1. 翻译成简洁准确的中文标题（如果已是中文则保持或优化）
  2. 从以下分类中选择最合适的一个：{', '.join(CATEGORIES)}

  请严格按照以下JSON格式返回，不要有任何其他内容：
  [
    {{"title_zh": "中文标题", "category": "分类"}},
    ...
  ]

  新闻列表：
  {items_text}"""

          try:
              with httpx.Client(timeout=60) as client:
                  resp = client.post(
                      DEEPSEEK_URL,
                      headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                      json={
                          "model": "deepseek-chat",
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 2000,
                          "temperature": 0.3
                      }
                  )
              if resp.status_code == 200:
                  content = resp.json()["choices"][0]["message"]["content"]
                  json_match = re.search(r'\[.*\]', content, re.DOTALL)
                  if json_match:
                      parsed = json.loads(json_match.group())
                      for j, item in enumerate(parsed):
                          if j < len(batch):
                              batch[j]['title_zh'] = item.get('title_zh', batch[j]['title_original'])
                              batch[j]['category'] = item.get('category', '其他')
          except Exception as e:
              print(f"AI classification error: {e}")
              for a in batch:
                  if not a.get('title_zh'):
                      a['title_zh'] = a['title_original']
                  if not a.get('category'):
                      a['category'] = '其他'

          results.extend(batch)

      return results

  if __name__ == '__main__':
      print("Fetching RSS feeds...")
      articles = fetch_all_feeds()
      print(f"Fetched {len(articles)} articles")

      print("Classifying and translating...")
      articles = classify_and_translate(articles)

      output = {
          'updated_at': datetime.now().isoformat(),
          'articles': articles
      }

      with open('news.json', 'w', encoding='utf-8') as f:
          json.dump(output, f, ensure_ascii=False, indent=2)

      print(f"Saved {len(articles)} articles to news.json")
