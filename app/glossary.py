"""Fixed translations of the category labels, passed to the translation model so the same
category is always called the same thing. Hand-written; to be reviewed by community translators
(like the fixed labels in languages.py). "Thefts over $5,000" is left out on purpose: a digit in a
glossary term encourages the model to type numbers instead of the protected ⟦N⟧ placeholders.
"""
from __future__ import annotations

KEYS = ["Assaults", "Break-ins", "Stolen vehicles", "Theft from vehicles", "Robberies",
        "Bike thefts", "Shootings", "Homicides"]

GLOSSARY: dict[str, list[str]] = {
    "ur": ["حملے", "نقب زنی", "چوری شدہ گاڑیاں", "گاڑیوں سے چوری", "ڈکیتی", "سائیکل چوری", "فائرنگ", "قتل"],
    "ar": ["الاعتداءات", "السطو على المنازل والمحلات", "السيارات المسروقة", "السرقة من السيارات",
           "السرقة بالإكراه", "سرقة الدراجات", "حوادث إطلاق النار", "جرائم القتل"],
    "ps": ["بریدونه", "د غلا لپاره ننوتل", "غلا شوي موټرونه", "له موټرو غلا", "لوټ مار", "د بایسکل غلا", "ډزې", "وژنې"],
    "fa": ["ضرب و جرح", "سرقت از منزل و مغازه", "خودروهای سرقت‌شده", "سرقت از خودرو", "زورگیری",
           "سرقت دوچرخه", "تیراندازی", "قتل"],
    "zh-Hant": ["襲擊", "爆竊", "汽車被盜", "車內財物被盜", "搶劫", "單車被盜", "槍擊", "兇殺"],
    "zh-Hans": ["袭击", "入室盗窃", "汽车被盗", "车内财物被盗", "抢劫", "自行车被盗", "枪击", "凶杀"],
    "ta": ["தாக்குதல்கள்", "வீடு உடைப்புகள்", "திருடப்பட்ட வாகனங்கள்", "வாகனங்களிலிருந்து திருட்டு",
           "கொள்ளைகள்", "மிதிவண்டி திருட்டுகள்", "துப்பாக்கிச் சூடுகள்", "கொலைகள்"],
    "ru": ["Нападения", "Кражи со взломом", "Угоны автомобилей", "Кражи из автомобилей", "Грабежи",
           "Кражи велосипедов", "Случаи стрельбы", "Убийства"],
    "fil": ["Pananakit", "Panloloob", "Mga ninakaw na sasakyan", "Pagnanakaw mula sa sasakyan", "Panghoholdap",
            "Pagnanakaw ng bisikleta", "Pamamaril", "Pagpatay"],
    "it": ["Aggressioni", "Furti con scasso", "Veicoli rubati", "Furti da veicoli", "Rapine",
           "Furti di biciclette", "Sparatorie", "Omicidi"],
    "pt-pt": ["Agressões", "Arrombamentos", "Veículos furtados", "Furtos em veículos", "Roubos",
              "Furtos de bicicletas", "Tiroteios", "Homicídios"],
    "es": ["Agresiones", "Robos con allanamiento", "Vehículos robados", "Robos en vehículos", "Robos con violencia",
           "Robos de bicicletas", "Tiroteos", "Homicidios"],
    "ko": ["폭행", "주거침입 절도", "차량 도난", "차량 내 물품 도난", "강도", "자전거 도난", "총격", "살인"],
    # Hand-written (pl, uk, el, vi), or drafted with Azure and fixed by hand where it went wrong (bn, gu, pa,
    # so: e.g. Punjabi "massacre" for homicides, Ukrainian "hacks" for break-ins, Somali "events" for
    # robberies). All need a community translator's review, like the other labels.
    "pl": ["Napaści", "Włamania", "Skradzione pojazdy", "Kradzieże z pojazdów", "Rozboje",
           "Kradzieże rowerów", "Strzelaniny", "Zabójstwa"],
    "uk": ["Напади", "Крадіжки зі зломом", "Викрадені автомобілі", "Крадіжки з автомобілів", "Пограбування",
           "Крадіжки велосипедів", "Стрілянина", "Вбивства"],
    "el": ["Επιθέσεις", "Διαρρήξεις", "Κλεμμένα οχήματα", "Κλοπές από οχήματα", "Ληστείες",
           "Κλοπές ποδηλάτων", "Πυροβολισμοί", "Ανθρωποκτονίες"],
    "vi": ["Hành hung", "Đột nhập", "Xe bị đánh cắp", "Trộm đồ trong xe", "Cướp tài sản",
           "Trộm xe đạp", "Các vụ nổ súng", "Các vụ giết người"],
    "bn": ["হামলা", "ঘরে ঢুকে চুরি", "চুরি যাওয়া গাড়ি", "গাড়ির ভেতর থেকে চুরি", "ডাকাতি",
           "সাইকেল চুরি", "গুলি চালানো", "হত্যাকাণ্ড"],
    "gu": ["હુમલાઓ", "ઘરફોડ ચોરી", "ચોરાયેલા વાહનો", "વાહનોની અંદરથી ચોરી", "લૂંટ",
           "સાયકલ ચોરી", "ગોળીબાર", "હત્યાઓ"],
    "pa": ["ਹਮਲੇ", "ਸੰਨ੍ਹ ਲਾ ਕੇ ਚੋਰੀ", "ਚੋਰੀ ਹੋਏ ਵਾਹਨ", "ਵਾਹਨਾਂ ਦੇ ਅੰਦਰੋਂ ਚੋਰੀ", "ਡਕੈਤੀਆਂ",
           "ਸਾਈਕਲ ਚੋਰੀ", "ਗੋਲੀਬਾਰੀ", "ਕਤਲ"],
    "so": ["Weerarada", "Jabsashada guryaha", "Gaadiidka la xaday", "Xatooyada alaabta gudaha gaadiidka", "Dhaca",
           "Xatooyada baaskiilada", "Toogashooyin", "Dilalka"],
    "fr": ["Voies de fait", "Introductions par effraction", "Véhicules volés", "Vols dans des véhicules", "Vols qualifiés",
           "Vols de vélos", "Coups de feu", "Homicides"],
}
assert all(len(v) == len(KEYS) for v in GLOSSARY.values())

# "Thefts over $5,000" for the Azure path only (label_prefix), never in a model prompt: a digit in a prompt
# glossary invites the model to type numbers instead of placeholders. Found by the back-translation check:
# Portuguese "Roubos" (robbery, not theft) and an ambiguous Spanish "Robos". The amount is written exactly
# as in English so the number check matches it.
THEFT_OVER = {
    "pt-pt": "Furtos de mais de $5,000", "es": "Hurtos de más de $5,000", "it": "Furti di oltre $5,000",
    "fr": "Vols de plus de $5,000", "pl": "Kradzieże powyżej $5,000", "ru": "Кражи на сумму свыше $5,000",
    "uk": "Крадіжки на суму понад $5,000", "el": "Κλοπές άνω των $5,000",
}


def terms_for(code: str, english_text: str) -> dict[str, str]:
    """Glossary entries for category labels that actually appear in this English text."""
    row = GLOSSARY.get(code)
    if not row:
        return {}
    return {k: t for k, t in zip(KEYS, row) if k in english_text}


def usage(code: str, english_doc_text: str, translated_text: str) -> tuple[int, int]:
    """(terms used, terms expected): a quality signal, not a gate (inflection varies)."""
    terms = terms_for(code, english_doc_text)
    used = sum(1 for t in terms.values() if t.lower() in translated_text.lower())
    return used, len(terms)


def label_prefix(code: str, line: str) -> tuple[str, str] | None:
    """A bulletin line starts with its category label ("Theft from vehicles: 5 reports..."). Return
    (fixed translation of the label, rest of the line) so a translator can't change the category
    (Azure once wrote "Theft from vehicles" as "vehicle theft" in French)."""
    if code in THEFT_OVER and line.startswith("Thefts over $5,000:"):
        return THEFT_OVER[code], line[len("Thefts over $5,000"):]
    row = GLOSSARY.get(code)
    if not row:
        return None
    for k, t in zip(KEYS, row):
        if line.startswith(k + ":"):
            return t, line[len(k):]
    return None
