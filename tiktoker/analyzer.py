import re
from collections import Counter

STOP = {
    "это","как","что","для","или","про","при","все","всё","его","она","они","там",
    "где","когда","который","которые","видео","youtube","the","and","with","from",
    "this","that","your","you","are","was","http","https","www","com"
}

def analyze(info: dict, cfg: dict) -> str:
    required = cfg.get("required", ["#fyp", "#рек", "#рекомендации"])
    max_auto = int(cfg.get("max_auto", 7))
    title = info.get("title") or ""
    desc = info.get("description") or ""
    text = (title + " " + desc).lower()
    words = re.findall(r"[а-яёa-z0-9]{3,}", text, flags=re.I)
    counts = Counter(w for w in words if w not in STOP and not w.isdigit())

    tags = []
    for tag in required:
        tag = tag if tag.startswith("#") else "#" + tag
        if tag.lower() not in {x.lower() for x in tags}:
            tags.append(tag)

    for word, _ in counts.most_common(max_auto * 3):
        tag = "#" + word.replace("_", "")
        if tag.lower() not in {x.lower() for x in tags}:
            tags.append(tag)
        if len(tags) >= len(required) + max_auto:
            break
    return " ".join(tags)
