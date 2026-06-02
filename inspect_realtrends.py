import re
import urllib.request

url = "https://www.realtrends.com/ranking/best-real-estate-agents-united-states/individuals-by-volume/"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
print("length:", len(html))
print("__NEXT_DATA__:", "__NEXT_DATA__" in html)
for pat in [r"/api/[a-zA-Z0-9/_-]+", r"ranking[^\"']*", r"graphql[^\"']*"]:
    hits = re.findall(pat, html, re.I)
    print(pat, hits[:8])
