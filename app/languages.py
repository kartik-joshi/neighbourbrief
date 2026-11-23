"""Language catalogue: codes, scripts, direction, fonts and fixed bulletin labels.

Labels are short fixed strings (headings, notices) translated once by hand so every bulletin's
frame is stable; only the officer/summary text goes through machine translation.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata

from babel.dates import format_skeleton

from app.config import CONFIG_DIR

FONT_ARABIC = "Noto Naskh Arabic"
FONT_URDU = "Noto Nastaliq Urdu"

LANGUAGES: dict[str, dict] = {
    "en": {"name": "English", "native": "English", "scripts": ["Latin"], "rtl": False,
           "font": "Noto Sans", "babel": "en_CA", "llm_name": "English"},
    "fr": {"name": "French", "native": "Français", "scripts": ["Latin"], "rtl": False,
           "font": "Noto Sans", "babel": "fr_CA", "llm_name": "Canadian French"},
    "ur": {"name": "Urdu", "native": "اردو", "scripts": ["Arabic"], "rtl": True,
           "font": FONT_URDU, "babel": "ur", "llm_name": "Urdu (in Urdu/Perso-Arabic script, never Devanagari/Hindi)"},
    "ps": {"name": "Pashto", "native": "پښتو", "scripts": ["Arabic"], "rtl": True,
           "font": FONT_ARABIC, "babel": "ps", "llm_name": "Pashto (in Pashto Arabic script)"},
    "ar": {"name": "Arabic", "native": "العربية", "scripts": ["Arabic"], "rtl": True,
           "font": FONT_ARABIC, "babel": "ar", "llm_name": "Modern Standard Arabic"},
    "fa": {"name": "Persian", "native": "فارسی", "scripts": ["Arabic"], "rtl": True,
           "font": FONT_ARABIC, "babel": "fa", "llm_name": "Persian (Farsi, in Persian script)"},
    "zh-Hant": {"name": "Chinese (Traditional)", "native": "繁體中文",
                "scripts": ["Han"], "rtl": False, "font": "Noto Sans TC", "babel": "zh_Hant",
                "llm_name": "Traditional Chinese (as written in Hong Kong)"},
    "zh-Hans": {"name": "Chinese (Simplified)", "native": "简体中文",
                "scripts": ["Han"], "rtl": False, "font": "Noto Sans SC", "babel": "zh_Hans",
                "llm_name": "Simplified Chinese"},
    "ta": {"name": "Tamil", "native": "தமிழ்", "scripts": ["Tamil"], "rtl": False,
           "font": "Noto Sans Tamil", "babel": "ta", "llm_name": "Tamil (in Tamil script)"},
    "ru": {"name": "Russian", "native": "Русский", "scripts": ["Cyrillic"], "rtl": False,
           "font": "Noto Sans", "babel": "ru", "llm_name": "Russian"},
    "fil": {"name": "Filipino (Tagalog)", "native": "Filipino", "scripts": ["Latin"], "rtl": False,
            "font": "Noto Sans", "babel": "fil", "llm_name": "Filipino (Tagalog)"},
    "it": {"name": "Italian", "native": "Italiano", "scripts": ["Latin"], "rtl": False,
           "font": "Noto Sans", "babel": "it", "llm_name": "Italian"},
    "pt-pt": {"name": "Portuguese (Portugal)", "native": "Português", "scripts": ["Latin"],
              "rtl": False, "font": "Noto Sans", "babel": "pt_PT", "llm_name": "European Portuguese"},
    "es": {"name": "Spanish", "native": "Español", "scripts": ["Latin"], "rtl": False,
           "font": "Noto Sans", "babel": "es", "llm_name": "Spanish"},
    "ko": {"name": "Korean", "native": "한국어", "scripts": ["Hangul", "Han"], "rtl": False,
           "font": "Noto Sans KR", "babel": "ko", "llm_name": "Korean (in Hangul)"},
    "vi": {"name": "Vietnamese", "native": "Tiếng Việt", "scripts": ["Latin"], "rtl": False,
           "font": "Noto Sans", "babel": "vi", "llm_name": "Vietnamese (with all tone marks)"},
    "pl": {"name": "Polish", "native": "Polski", "scripts": ["Latin"], "rtl": False,
           "font": "Noto Sans", "babel": "pl", "llm_name": "Polish"},
    "bn": {"name": "Bengali", "native": "বাংলা", "scripts": ["Bengali"], "rtl": False,
           "font": "Noto Sans Bengali", "babel": "bn", "llm_name": "Bengali (Bangla, in Bengali script)"},
    "el": {"name": "Greek", "native": "Ελληνικά", "scripts": ["Greek"], "rtl": False,
           "font": "Noto Sans", "babel": "el", "llm_name": "Modern Greek"},
    "gu": {"name": "Gujarati", "native": "ગુજરાતી", "scripts": ["Gujarati"], "rtl": False,
           "font": "Noto Sans Gujarati", "babel": "gu", "llm_name": "Gujarati (in Gujarati script)"},
    "uk": {"name": "Ukrainian", "native": "Українська", "scripts": ["Cyrillic"], "rtl": False,
           "font": "Noto Sans", "babel": "uk", "llm_name": "Ukrainian (never Russian)"},
    "pa": {"name": "Punjabi", "native": "ਪੰਜਾਬੀ", "scripts": ["Gurmukhi"], "rtl": False,
           "font": "Noto Sans Gurmukhi", "babel": "pa_Guru",
           "llm_name": "Punjabi (in Gurmukhi script, never Shahmukhi or Devanagari)"},
    "so": {"name": "Somali", "native": "Soomaali", "scripts": ["Latin"], "rtl": False,
           "font": "Noto Sans", "babel": "so", "llm_name": "Somali"},
}

# Fixed bulletin labels. English is the fallback for any missing key.
LABELS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Neighbourhood Safety Update",
        "reports": "Reports to police",
        "from_officers": "From your officers",
        "ncos": "Your Neighbourhood Community Officers",
        "division": "Contact your police division",
        "meeting": "Next community meeting",
        "verified": "Numbers checked against TPS open data",
        "not_verified": "Some numbers need a manual check",
        "notice": "AI-assisted translation, not yet reviewed by a human translator. This translation may contain mistakes.",
        "reviewed": "Translation reviewed by a human translator.",
        "services": "Local support services",
        "services_note": "From the City's community services list. Please call ahead to confirm.",
        "auto_note": "Automatic summary from open data. No note from your officers this month.",
        "fb_question": "What should your officers focus on next month?",
        "vs": "vs",
        "emergency": "Emergency: 911 · Non-emergency: 416-808-2222",
    },
    "fr": {
        "title": "Bulletin de sécurité du quartier",
        "reports": "Signalements à la police",
        "from_officers": "De vos agents",
        "ncos": "Vos agents communautaires de quartier",
        "division": "Communiquez avec votre division de police",
        "meeting": "Prochaine réunion communautaire",
        "verified": "Chiffres vérifiés avec les données ouvertes du SPT",
        "not_verified": "Certains chiffres doivent être vérifiés à la main",
        "notice": "Traduction assistée par l'IA, pas encore révisée par un traducteur. Cette traduction peut contenir des erreurs.",
        "reviewed": "Traduction révisée par un traducteur.",
        "services": "Services de soutien locaux",
        "services_note": "Tiré de la liste des services communautaires de la Ville. Appelez avant de vous déplacer.",
        "auto_note": "Résumé automatique à partir des données ouvertes. Aucune note de vos agents ce mois-ci.",
        "fb_question": "Sur quoi vos agents devraient-ils se concentrer le mois prochain?",
        "vs": "c.",
        "emergency": "Urgence : 911 · Non urgent : 416-808-2222",
    },
    "ur": {
        "title": "محلے کی حفاظت کی تازہ معلومات",
        "reports": "پولیس کو رپورٹس",
        "from_officers": "آپ کے افسران کی طرف سے",
        "ncos": "آپ کے محلے کے کمیونٹی افسران",
        "division": "اپنے پولیس ڈویژن سے رابطہ کریں",
        "meeting": "اگلی کمیونٹی میٹنگ",
        "verified": "اعداد و شمار TPS کے اوپن ڈیٹا سے جانچے گئے",
        "not_verified": "کچھ اعداد کی دستی جانچ ضروری ہے",
        "notice": "مصنوعی ذہانت کی مدد سے ترجمہ، ابھی کسی انسانی مترجم نے نظرثانی نہیں کی۔ اس ترجمے میں غلطیاں ہو سکتی ہیں۔",
        "reviewed": "اس ترجمے پر ایک انسانی مترجم نے نظرثانی کی ہے۔",
        "services": "مقامی امدادی خدمات",
        "services_note": "شہر کی کمیونٹی خدمات کی فہرست سے، جانے سے پہلے فون کر کے تصدیق کریں۔",
        "auto_note": "اوپن ڈیٹا سے خودکار خلاصہ۔ اس مہینے آپ کے افسران کی طرف سے کوئی نوٹ نہیں۔",
        "fb_question": "اگلے مہینے آپ کے افسران کو کس چیز پر توجہ دینی چاہیے؟",
        "vs": "بمقابلہ",
        "emergency": "ایمرجنسی: 911 · غیر ہنگامی: 416-808-2222",
    },
    "ps": {
        "title": "د ګاونډ د خوندیتوب تازه معلومات",
        "reports": "پولیسو ته راپورونه",
        "from_officers": "ستاسو د افسرانو له خوا",
        "ncos": "ستاسو د ګاونډ ټولنیز افسران",
        "division": "د خپلې پولیسي څانګې سره اړیکه ونیسئ",
        "meeting": "راتلونکې ټولنیزه غونډه",
        "verified": "شمېرې د TPS له خلاص معلوماتو سره وڅېړل شوې",
        "not_verified": "ځینې شمېرې لاسي کتنې ته اړتیا لري",
        "notice": "د مصنوعي ځیرکتیا په مرسته ژباړه، لا تر اوسه د انساني ژباړونکي له خوا نه ده کتل شوې. دا ژباړه کېدای شی تېروتنې ولری.",
        "reviewed": "دا ژباړه د یوه انساني ژباړونکي له خوا کتل شوې ده.",
        "services": "سیمه ییز ملاتړي خدمتونه",
        "services_note": "د ښار د ټولنیزو خدمتونو له لېست څخه، د تګ نه مخکې زنګ ووهئ او ډاډ ترلاسه کړئ.",
        "auto_note": "د خلاصو معلوماتو څخه اتومات لنډیز. ستاسو د افسرانو له خوا په دې میاشت کې هیڅ یادښت نشته.",
        "fb_question": "ستاسو افسران باید راتلونکې میاشت پر څه تمرکز وکړی؟",
        "vs": "په مقابل کې",
        "emergency": "بیړني حالت: 911 · غیر بیړني: 416-808-2222",
    },
    "ar": {
        "title": "تحديث السلامة في الحي",
        "reports": "البلاغات المقدمة إلى الشرطة",
        "from_officers": "من ضباطكم",
        "ncos": "ضباط المجتمع في حيّكم",
        "division": "تواصلوا مع قسم الشرطة التابع لكم",
        "meeting": "الاجتماع المجتمعي القادم",
        "verified": "تم التحقق من الأرقام مقابل بيانات TPS المفتوحة",
        "not_verified": "بعض الأرقام تحتاج إلى مراجعة يدوية",
        "notice": "ترجمة بمساعدة الذكاء الاصطناعي، لم يراجعها مترجم بشري بعد. قد تحتوي هذه الترجمة على أخطاء.",
        "reviewed": "تمت مراجعة هذه الترجمة من قبل مترجم بشري.",
        "services": "خدمات الدعم المحلية",
        "services_note": "من قائمة الخدمات المجتمعية في المدينة، اتصلوا مسبقًا للتأكد.",
        "auto_note": "ملخص تلقائي من البيانات المفتوحة. لا توجد ملاحظة من ضباطكم هذا الشهر.",
        "fb_question": "على ماذا يجب أن يركز ضباط الشرطة في حيّكم الشهر القادم؟",
        "vs": "مقابل",
        "emergency": "الطوارئ: 911 · لغير الطوارئ: 416-808-2222",
    },
    "fa": {
        "title": "به‌روزرسانی ایمنی محله",
        "reports": "گزارش‌ها به پلیس",
        "from_officers": "از طرف افسران شما",
        "ncos": "افسران اجتماعی محله شما",
        "division": "با بخش پلیس خود تماس بگیرید",
        "meeting": "جلسه بعدی محله",
        "verified": "اعداد با داده‌های باز TPS بررسی شده‌اند",
        "not_verified": "برخی اعداد نیاز به بررسی دستی دارند",
        "notice": "ترجمه با کمک هوش مصنوعی، هنوز توسط مترجم انسانی بازبینی نشده است. این ترجمه ممکن است شامل اشتباهاتی باشد.",
        "reviewed": "این ترجمه توسط یک مترجم انسانی بازبینی شده است.",
        "services": "خدمات حمایتی محلی",
        "services_note": "از فهرست خدمات اجتماعی شهر، پیش از مراجعه تماس بگیرید.",
        "auto_note": "خلاصه خودکار از داده های باز. این ماه هیچ یادداشتی از افسران شما نیست.",
        "fb_question": "افسران شما باید ماه آینده روی چه موضوعاتی تمرکز کنند؟",
        "vs": "در برابر",
        "emergency": "اضطراری: 911 · غیراضطراری: 416-808-2222",
    },
    "zh-Hant": {
        "title": "社區安全簡報",
        "reports": "向警方報案",
        "from_officers": "社區警員的話",
        "ncos": "您的社區警員",
        "division": "聯絡您的警區",
        "meeting": "下次社區會議",
        "verified": "數字已與多倫多警察局開放數據核對",
        "not_verified": "部分數字需要人工核對",
        "notice": "人工智能輔助翻譯，尚未經人工翻譯員審閱。此翻譯可能包含錯誤。",
        "reviewed": "本譯文已經人工翻譯員審閱。",
        "services": "本區支援服務",
        "services_note": "資料來自市政府社區服務名單，前往前請先致電確認。",
        "auto_note": "根據開放資料自動產生的摘要。本月沒有警員留言。",
        "fb_question": "下個月，社區警員應該重點關注什麼？",
        "vs": "對比",
        "emergency": "緊急情況：911 · 非緊急：416-808-2222",
    },
    "zh-Hans": {
        "title": "社区安全简报",
        "reports": "向警方报案",
        "from_officers": "社区警员的话",
        "ncos": "您的社区警员",
        "division": "联系您的警区",
        "meeting": "下次社区会议",
        "verified": "数字已与多伦多警察局开放数据核对",
        "not_verified": "部分数字需要人工核对",
        "notice": "人工智能辅助翻译，尚未经人工翻译员审阅。此翻译可能存在错误。",
        "reviewed": "本译文已经人工翻译员审阅。",
        "services": "本区支援服务",
        "services_note": "资料来自市政府社区服务名单，前往前请先致电确认。",
        "auto_note": "根据开放数据自动生成的摘要。本月没有警员留言。",
        "fb_question": "下个月，社区警员应该重点关注什么？",
        "vs": "对比",
        "emergency": "紧急情况：911 · 非紧急：416-808-2222",
    },
    "ta": {
        "title": "சுற்றுப்புற பாதுகாப்பு செய்தி",
        "reports": "காவல்துறையிடம் அளிக்கப்பட்ட புகார்கள்",
        "from_officers": "உங்கள் அதிகாரிகளிடமிருந்து",
        "ncos": "உங்கள் சுற்றுப்புற சமூக அதிகாரிகள்",
        "division": "உங்கள் காவல் பிரிவைத் தொடர்பு கொள்ளுங்கள்",
        "meeting": "அடுத்த சமூகக் கூட்டம்",
        "verified": "எண்கள் TPS திறந்த தரவுடன் சரிபார்க்கப்பட்டன",
        "not_verified": "சில எண்களைக் கைமுறையாகச் சரிபார்க்க வேண்டும்",
        "notice": "செயற்கை நுண்ணறிவு உதவியுடன் மொழிபெயர்க்கப்பட்டது, மனித மொழிபெயர்ப்பாளர் இன்னும் சரிபார்க்கவில்லை. இந்த மொழிபெயர்ப்பில் பிழைகள் இருக்கலாம்.",
        "reviewed": "இந்த மொழிபெயர்ப்பை ஒரு மனித மொழிபெயர்ப்பாளர் சரிபார்த்துள்ளார்.",
        "services": "உள்ளூர் உதவி சேவைகள்",
        "services_note": "நகரின் சமூக சேவைகள் பட்டியலிலிருந்து, செல்லும் முன் அழைத்து உறுதிப்படுத்துங்கள்.",
        "auto_note": "திறந்த தரவிலிருந்து தானியங்கி சுருக்கம். இந்த மாதம் உங்கள் அதிகாரிகளிடமிருந்து குறிப்பு இல்லை.",
        "fb_question": "அடுத்த மாதம் உங்கள் அதிகாரிகள் எதில் கவனம் செலுத்த வேண்டும்?",
        "vs": "எதிர்",
        "emergency": "அவசரம்: 911 · அவசரமற்றது: 416-808-2222",
    },
    "ru": {
        "title": "Новости безопасности района",
        "reports": "Заявления в полицию",
        "from_officers": "От ваших полицейских",
        "ncos": "Ваши районные полицейские",
        "division": "Свяжитесь с вашим отделом полиции",
        "meeting": "Следующее собрание жителей",
        "verified": "Цифры сверены с открытыми данными TPS",
        "not_verified": "Некоторые цифры нужно проверить вручную",
        "notice": "Перевод выполнен с помощью ИИ, ещё не проверен переводчиком. Этот перевод может содержать ошибки.",
        "reviewed": "Перевод проверен переводчиком.",
        "services": "Местные службы поддержки",
        "services_note": "Из городского списка социальных служб, перед визитом позвоните и уточните.",
        "auto_note": "Автоматическая сводка по открытым данным. В этом месяце сообщения от полицейских нет.",
        "fb_question": "На чём полиции стоит сосредоточиться в вашем районе в следующем месяце?",
        "vs": "против",
        "emergency": "Экстренные случаи: 911 · Не экстренные: 416-808-2222",
    },
    "fil": {
        "title": "Balita sa Kaligtasan ng Komunidad",
        "reports": "Mga ulat sa pulisya",
        "from_officers": "Mula sa inyong mga opisyal",
        "ncos": "Ang inyong mga Neighbourhood Community Officer",
        "division": "Makipag-ugnayan sa inyong police division",
        "meeting": "Susunod na pulong ng komunidad",
        "verified": "Sinuri ang mga numero laban sa open data ng TPS",
        "not_verified": "May mga numerong kailangang suriin nang mano-mano",
        "notice": "Pagsasalin sa tulong ng AI, hindi pa nasusuri ng taong tagasalin. Maaaring may mga pagkakamali ang salin na ito.",
        "reviewed": "Sinuri ng taong tagasalin ang salin na ito.",
        "services": "Mga lokal na serbisyo ng tulong",
        "services_note": "Mula sa listahan ng mga serbisyong pangkomunidad ng Lungsod, tumawag muna para makasiguro.",
        "auto_note": "Awtomatikong buod mula sa open data. Walang sulat mula sa inyong mga opisyal ngayong buwan.",
        "fb_question": "Ano ang dapat pagtuunan ng pansin ng inyong mga opisyal sa susunod na buwan?",
        "vs": "kumpara sa",
        "emergency": "Emergency: 911 · Hindi emergency: 416-808-2222",
    },
    "it": {
        "title": "Aggiornamento sulla sicurezza del quartiere",
        "reports": "Denunce alla polizia",
        "from_officers": "Dai vostri agenti",
        "ncos": "I vostri agenti di quartiere",
        "division": "Contattate la vostra divisione di polizia",
        "meeting": "Prossimo incontro di quartiere",
        "verified": "Numeri verificati con i dati aperti TPS",
        "not_verified": "Alcuni numeri richiedono un controllo manuale",
        "notice": "Traduzione assistita dall'IA, non ancora revisionata da un traduttore. Questa traduzione può contenere errori.",
        "reviewed": "Traduzione revisionata da un traduttore.",
        "services": "Servizi di supporto locali",
        "services_note": "Dall'elenco dei servizi comunitari della città, chiamate prima per conferma.",
        "auto_note": "Riepilogo automatico dai dati aperti. Questo mese non c'è una nota degli agenti.",
        "fb_question": "Su cosa dovrebbero concentrarsi gli agenti il prossimo mese?",
        "vs": "contro",
        "emergency": "Emergenze: 911 · Non urgenti: 416-808-2222",
    },
    "pt-pt": {
        "title": "Atualização de segurança do bairro",
        "reports": "Participações à polícia",
        "from_officers": "Dos seus agentes",
        "ncos": "Os seus agentes comunitários do bairro",
        "division": "Contacte a sua divisão de polícia",
        "meeting": "Próxima reunião comunitária",
        "verified": "Números verificados com os dados abertos da TPS",
        "not_verified": "Alguns números precisam de verificação manual",
        "notice": "Tradução assistida por IA, ainda não revista por um tradutor humano. Esta tradução pode conter erros.",
        "reviewed": "Tradução revista por um tradutor humano.",
        "services": "Serviços de apoio locais",
        "services_note": "Da lista de serviços comunitários da cidade, ligue antes para confirmar.",
        "auto_note": "Resumo automático a partir de dados abertos. Este mês não há nota dos agentes.",
        "fb_question": "Em que devem os seus agentes focar-se no próximo mês?",
        "vs": "vs",
        "emergency": "Emergência: 911 · Não urgente: 416-808-2222",
    },
    "es": {
        "title": "Boletín de seguridad del vecindario",
        "reports": "Denuncias a la policía",
        "from_officers": "De sus agentes",
        "ncos": "Sus agentes comunitarios del vecindario",
        "division": "Contacte a su división de policía",
        "meeting": "Próxima reunión comunitaria",
        "verified": "Cifras verificadas con los datos abiertos de TPS",
        "not_verified": "Algunas cifras requieren revisión manual",
        "notice": "Traducción asistida por IA, aún no revisada por un traductor humano. Esta traducción puede contener errores.",
        "reviewed": "Traducción revisada por un traductor humano.",
        "services": "Servicios de apoyo locales",
        "services_note": "De la lista de servicios comunitarios de la ciudad, llame antes para confirmar.",
        "auto_note": "Resumen automático a partir de datos abiertos. Este mes no hay nota de los agentes.",
        "fb_question": "¿En qué deberían centrarse sus agentes el próximo mes?",
        "vs": "vs",
        "emergency": "Emergencias: 911 · No urgente: 416-808-2222",
    },
    "ko": {
        "title": "동네 안전 소식",
        "reports": "경찰 신고 건수",
        "from_officers": "담당 경찰관의 말씀",
        "ncos": "우리 동네 커뮤니티 담당 경찰관",
        "division": "관할 경찰서에 연락하세요",
        "meeting": "다음 주민 모임",
        "verified": "숫자는 TPS 공개 데이터와 대조 확인됨",
        "not_verified": "일부 숫자는 직접 확인이 필요합니다",
        "notice": "AI 지원 번역, 아직 전문 번역가의 검토를 거치지 않았습니다. 이 번역에는 오류가 있을 수 있습니다.",
        "reviewed": "이 번역은 전문 번역가의 검토를 거쳤습니다.",
        "services": "지역 지원 서비스",
        "services_note": "시 커뮤니티 서비스 목록에서 발췌, 방문 전에 전화로 확인하세요.",
        "auto_note": "오픈 데이터로 자동 작성한 요약입니다. 이번 달에는 경찰관의 메모가 없습니다.",
        "fb_question": "다음 달에는 경찰관들이 무엇에 집중해야 할까요?",
        "vs": "대",
        "emergency": "긴급: 911 · 비긴급: 416-808-2222",
    },
    # Added Oct 23, 2026: drafted with Azure Translator, then edited (police terms, word order).
    # Like every label here, they still need a review by a community translator.
    "vi": {
        "title": "Cập nhật an toàn khu phố",
        "reports": "Trình báo với cảnh sát",
        "from_officers": "Từ các cảnh sát của bạn",
        "ncos": "Cảnh sát cộng đồng khu phố của bạn",
        "division": "Liên hệ đồn cảnh sát khu vực của bạn",
        "meeting": "Buổi họp cộng đồng tiếp theo",
        "verified": "Các con số đã được đối chiếu với dữ liệu mở của TPS",
        "not_verified": "Một số con số cần được kiểm tra thủ công",
        "notice": "Bản dịch có hỗ trợ của AI, chưa được người dịch xem lại. Bản dịch này có thể chứa lỗi.",
        "reviewed": "Bản dịch đã được người dịch xem lại.",
        "services": "Dịch vụ hỗ trợ tại địa phương",
        "services_note": "Từ danh sách dịch vụ cộng đồng của thành phố. Vui lòng gọi trước để xác nhận.",
        "auto_note": "Tóm tắt tự động từ dữ liệu mở. Tháng này không có ghi chú từ các sĩ quan cảnh sát.",
        "fb_question": "Tháng tới, các cảnh sát khu phố nên tập trung vào điều gì?",
        "vs": "so với",
        "emergency": "Khẩn cấp: 911 · Không khẩn cấp: 416-808-2222",
    },
    "pl": {
        "title": "Bezpieczeństwo w okolicy: aktualności",
        "reports": "Zgłoszenia na policję",
        "from_officers": "Od Twoich policjantów",
        "ncos": "Twoi policjanci dzielnicowi",
        "division": "Skontaktuj się ze swoim komisariatem",
        "meeting": "Następne spotkanie mieszkańców",
        "verified": "Liczby sprawdzone z otwartymi danymi TPS",
        "not_verified": "Niektóre liczby wymagają ręcznego sprawdzenia",
        "notice": "Tłumaczenie wspomagane przez AI, jeszcze niesprawdzone przez tłumacza. To tłumaczenie może zawierać błędy.",
        "reviewed": "Tłumaczenie sprawdzone przez tłumacza.",
        "services": "Lokalne usługi wsparcia",
        "services_note": "Z miejskiej listy usług społecznych. Przed wizytą prosimy zadzwonić i potwierdzić.",
        "auto_note": "Automatyczne podsumowanie z otwartych danych. W tym miesiącu brak notatki od funkcjonariuszy.",
        "fb_question": "Na czym funkcjonariusze powinni się skupić w przyszłym miesiącu?",
        "vs": "vs",
        "emergency": "Nagłe wypadki: 911 · Sprawy niepilne: 416-808-2222",
    },
    "bn": {
        "title": "এলাকার নিরাপত্তা আপডেট",
        "reports": "পুলিশের কাছে রিপোর্ট",
        "from_officers": "আপনার অফিসারদের পক্ষ থেকে",
        "ncos": "আপনার এলাকার কমিউনিটি পুলিশ অফিসার",
        "division": "আপনার পুলিশ ডিভিশনের সঙ্গে যোগাযোগ করুন",
        "meeting": "পরবর্তী কমিউনিটি সভা",
        "verified": "সংখ্যাগুলো TPS-এর ওপেন ডেটার সঙ্গে মিলিয়ে দেখা হয়েছে",
        "not_verified": "কিছু সংখ্যা হাতে মিলিয়ে দেখা দরকার",
        "notice": "এআই-সহায়ক অনুবাদ, এখনও কোনো অনুবাদক পর্যালোচনা করেননি। এই অনুবাদে ভুল থাকতে পারে।",
        "reviewed": "একজন অনুবাদক এই অনুবাদ পর্যালোচনা করেছেন।",
        "services": "স্থানীয় সহায়তা পরিষেবা",
        "services_note": "শহরের কমিউনিটি পরিষেবার তালিকা থেকে। যাওয়ার আগে ফোন করে নিশ্চিত হয়ে নিন।",
        "auto_note": "ওপেন ডেটা থেকে স্বয়ংক্রিয় সারসংক্ষেপ। এই মাসে আপনার অফিসারদের কাছ থেকে কোনও নোট নেই।",
        "fb_question": "আগামী মাসে আপনার কর্মকর্তাদের কী দিকে মনোনিবেশ করা উচিত?",
        "vs": "বনাম",
        "emergency": "জরুরি: 911 · জরুরি নয়: 416-808-2222",
    },
    "el": {
        "title": "Ενημέρωση για την ασφάλεια της γειτονιάς",
        "reports": "Αναφορές στην αστυνομία",
        "from_officers": "Από τους αστυνομικούς σας",
        "ncos": "Οι αστυνομικοί κοινότητας της γειτονιάς σας",
        "division": "Επικοινωνήστε με το αστυνομικό τμήμα της περιοχής σας",
        "meeting": "Επόμενη συνάντηση της κοινότητας",
        "verified": "Οι αριθμοί ελέγχθηκαν με τα ανοιχτά δεδομένα της TPS",
        "not_verified": "Ορισμένοι αριθμοί χρειάζονται έλεγχο με το χέρι",
        "notice": "Μετάφραση με τη βοήθεια AI, δεν έχει ελεγχθεί ακόμη από μεταφραστή. Αυτή η μετάφραση μπορεί να περιέχει λάθη.",
        "reviewed": "Η μετάφραση ελέγχθηκε από μεταφραστή.",
        "services": "Τοπικές υπηρεσίες υποστήριξης",
        "services_note": "Από τη λίστα κοινοτικών υπηρεσιών της πόλης. Τηλεφωνήστε πρώτα για επιβεβαίωση.",
        "auto_note": "Αυτόματη περίληψη από ανοιχτά δεδομένα. Κανένα σημείωμα από τους αξιωματικούς σας αυτόν τον μήνα.",
        "fb_question": "Σε τι πρέπει να επικεντρωθούν οι αξιωματικοί σας τον επόμενο μήνα;",
        "vs": "έναντι",
        "emergency": "Έκτακτη ανάγκη: 911 · Μη επείγοντα: 416-808-2222",
    },
    "gu": {
        "title": "પડોશની સલામતી અપડેટ",
        "reports": "પોલીસને થયેલા રિપોર્ટ",
        "from_officers": "તમારા અધિકારીઓ તરફથી",
        "ncos": "તમારા પડોશના કમ્યુનિટી પોલીસ અધિકારીઓ",
        "division": "તમારા પોલીસ વિભાગનો સંપર્ક કરો",
        "meeting": "આગામી સમુદાય બેઠક",
        "verified": "આંકડા TPS ના ઓપન ડેટા સાથે ચકાસેલા છે",
        "not_verified": "કેટલાક આંકડાની જાતે ચકાસણી જરૂરી છે",
        "notice": "એઆઈની મદદથી કરેલો અનુવાદ, હજી કોઈ અનુવાદકે તપાસ્યો નથી. આ અનુવાદમાં ભૂલો હોઈ શકે છે.",
        "reviewed": "અનુવાદકે આ અનુવાદ તપાસ્યો છે.",
        "services": "સ્થાનિક સહાય સેવાઓ",
        "services_note": "શહેરની સમુદાય સેવાઓની યાદીમાંથી. જતાં પહેલાં ફોન કરીને ખાતરી કરો.",
        "auto_note": "ઓપન ડેટામાંથી સ્વચાલિત સારાંશ. આ મહિને તમારા અધિકારીઓ તરફથી કોઈ નોંધ નથી.",
        "fb_question": "તમારા અધિકારીઓએ આવતા મહિને શેના પર ધ્યાન કેન્દ્રિત કરવું જોઈએ?",
        "vs": "સામે",
        "emergency": "કટોકટી: 911 · બિન-કટોકટી: 416-808-2222",
    },
    "uk": {
        "title": "Новини безпеки вашого району",
        "reports": "Повідомлення до поліції",
        "from_officers": "Від ваших поліцейських",
        "ncos": "Дільничні офіцери вашого району",
        "division": "Зверніться до свого відділу поліції",
        "meeting": "Наступні збори громади",
        "verified": "Цифри звірено з відкритими даними TPS",
        "not_verified": "Деякі цифри потрібно перевірити вручну",
        "notice": "Переклад за допомогою ШІ, ще не перевірений перекладачем. Цей переклад може містити помилки.",
        "reviewed": "Переклад перевірено перекладачем.",
        "services": "Місцеві служби підтримки",
        "services_note": "Зі списку громадських послуг міста. Будь ласка, зателефонуйте заздалегідь, щоб підтвердити.",
        "auto_note": "Автоматичний підсумок за відкритими даними. Цього місяця повідомлення від поліцейських немає.",
        "fb_question": "На чому поліції варто зосередитися у вашому районі наступного місяця?",
        "vs": "проти",
        "emergency": "Екстрені випадки: 911 · Не екстрені: 416-808-2222",
    },
    "pa": {  # Azure's draft had English inside a word ("ਕਮਿ communityਨਿਟੀ") and Latin full stops: fixed
        "title": "ਇਲਾਕੇ ਦੀ ਸੁਰੱਖਿਆ: ਤਾਜ਼ਾ ਜਾਣਕਾਰੀ",
        "reports": "ਪੁਲਿਸ ਨੂੰ ਕੀਤੀਆਂ ਰਿਪੋਰਟਾਂ",
        "from_officers": "ਤੁਹਾਡੇ ਪੁਲਿਸ ਅਫ਼ਸਰਾਂ ਵੱਲੋਂ",
        "ncos": "ਤੁਹਾਡੇ ਇਲਾਕੇ ਦੇ ਕਮਿਊਨਿਟੀ ਪੁਲਿਸ ਅਫ਼ਸਰ",
        "division": "ਆਪਣੀ ਪੁਲਿਸ ਡਿਵੀਜ਼ਨ ਨਾਲ ਸੰਪਰਕ ਕਰੋ",
        "meeting": "ਅਗਲੀ ਭਾਈਚਾਰਕ ਮੀਟਿੰਗ",
        "verified": "ਅੰਕੜੇ TPS ਦੇ ਓਪਨ ਡੇਟਾ ਨਾਲ ਮਿਲਾ ਕੇ ਜਾਂਚੇ ਗਏ",
        "not_verified": "ਕੁਝ ਅੰਕੜਿਆਂ ਦੀ ਹੱਥੀਂ ਜਾਂਚ ਜ਼ਰੂਰੀ ਹੈ",
        "notice": "ਏਆਈ ਦੀ ਮਦਦ ਨਾਲ ਕੀਤਾ ਅਨੁਵਾਦ, ਹਾਲੇ ਕਿਸੇ ਅਨੁਵਾਦਕ ਨੇ ਨਹੀਂ ਜਾਂਚਿਆ। ਇਸ ਅਨੁਵਾਦ ਵਿੱਚ ਗਲਤੀਆਂ ਹੋ ਸਕਦੀਆਂ ਹਨ।",
        "reviewed": "ਇਸ ਅਨੁਵਾਦ ਦੀ ਜਾਂਚ ਇੱਕ ਅਨੁਵਾਦਕ ਨੇ ਕੀਤੀ ਹੈ।",
        "services": "ਸਥਾਨਕ ਸਹਾਇਤਾ ਸੇਵਾਵਾਂ",
        "services_note": "ਸ਼ਹਿਰ ਦੀਆਂ ਕਮਿਊਨਿਟੀ ਸੇਵਾਵਾਂ ਦੀ ਸੂਚੀ ਵਿੱਚੋਂ। ਜਾਣ ਤੋਂ ਪਹਿਲਾਂ ਫ਼ੋਨ ਕਰਕੇ ਪੁਸ਼ਟੀ ਕਰੋ।",
        "auto_note": "ਖੁੱਲੇ ਡੇਟਾ ਤੋਂ ਆਟੋਮੈਟਿਕ ਸੰਖੇਪ. ਇਸ ਮਹੀਨੇ ਤੁਹਾਡੇ ਅਧਿਕਾਰੀਆਂ ਦਾ ਕੋਈ ਨੋਟ ਨਹੀਂ.",
        "fb_question": "ਤੁਹਾਡੇ ਅਧਿਕਾਰੀਆਂ ਨੂੰ ਅਗਲੇ ਮਹੀਨੇ ਕਿਸ ਗੱਲ 'ਤੇ ਧਿਆਨ ਦੇਣਾ ਚਾਹੀਦਾ ਹੈ?",
        "vs": "ਬਨਾਮ",
        "emergency": "ਐਮਰਜੈਂਸੀ: 911 · ਗੈਰ-ਐਮਰਜੈਂਸੀ: 416-808-2222",
    },
    "so": {
        "title": "Warbixinta Badbaadada Xaafadda",
        "reports": "Warbixinnada loo gudbiyay booliska",
        "from_officers": "Saraakiishaada booliska",
        "ncos": "Saraakiisha booliska bulshada ee xaafaddaada",
        "division": "La xiriir qaybta booliska ee deegaankaaga",
        "meeting": "Kulanka bulshada ee xiga",
        "verified": "Tirooyinka waxaa lagu hubiyay xogta furan ee TPS",
        "not_verified": "Tirooyinka qaar waxay u baahan yihiin hubin gacan ah",
        "notice": "Tarjumaad AI ka caawisay, welina aan dib loo eegin turjubaan bini'aadam ah. Turjumaaddan waxaa laga yaabaa inay khaladaad ku jirto.",
        "reviewed": "Turjumaadda waxaa dib u eegay turjubaan bini'aadam ah.",
        "services": "Adeegyada taageerada maxalliga ah",
        "services_note": "Liiska adeegyada bulshada ee magaalada. Fadlan wac ka hor si aad u xaqiijiso.",
        "auto_note": "Soo koobid toos ah oo ka timid xogta furan. Ma jirto qoraal ka yimid saraakiishaada bishan.",
        "fb_question": "Maxay saraakiishaadu diiradda saari doonaan bisha soo socota?",
        "vs": "vs",
        "emergency": "Degdeg: 911 · Aan degdeg ahayn: 416-808-2222",
    },
}


def labels(code: str) -> dict[str, str]:
    out = dict(LABELS["en"])
    out.update(LABELS.get(code, {}))
    return out


def lang_info(code: str) -> dict:
    info = dict(LANGUAGES.get(code) or {"name": code, "native": code, "scripts": ["Latin"],
                                        "rtl": False, "font": "Noto Sans", "babel": "en",
                                        "llm_name": code})
    info["code"] = code
    return info


def month_label(d: dt.date, code: str = "en") -> str:
    try:
        return format_skeleton("yMMMM", d, locale=lang_info(code)["babel"])
    except Exception:  # unknown locale in Babel
        return d.strftime("%B %Y")


def normalize_name(name: str) -> str:
    """Normalize a neighbourhood name for matching: drop '(55)', unify dashes, case, spaces."""
    s = unicodedata.normalize("NFKC", name or "")
    s = re.sub(r"[\u2018\u2019\u02bc\u0060\u00b4]", "'", s)  # typographic apostrophes → '
    s = re.sub(r"\(\s*\d+\s*\)", "", s)
    s = re.sub(r"[‐-―−\-]+", "-", s)
    s = re.sub(r"\s*-\s*", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def load_neighbourhood_languages() -> dict[str, list[dict]]:
    data = json.loads((CONFIG_DIR / "neighbourhood_languages.json").read_text(encoding="utf-8"))
    return {normalize_name(k): v for k, v in data["neighbourhoods"].items()}


CENSUS_FILE = CONFIG_DIR / "neighbourhood_languages_2021.json"
SUGGEST_MIN_SHARE = 3.0   # % of residents (mother tongue) to suggest a language
SUGGEST_MAX = 3
ALSO_MIN_SHARE = 2.0      # show untranslatable languages spoken by at least this share


def census_languages() -> dict:
    """2021 Census mother tongue per neighbourhood id (all 158), from the City's Neighbourhood
    Profiles; built by scripts/build_language_profiles.py."""
    try:
        return json.loads(CENSUS_FILE.read_text(encoding="utf-8"))["neighbourhoods"]
    except (OSError, ValueError, KeyError):
        return {}


def _census_entry(neighbourhood_name: str, hood_id: str | None) -> dict | None:
    data = census_languages()
    if hood_id and hood_id.zfill(3) in data:
        return data[hood_id.zfill(3)]
    target = normalize_name(neighbourhood_name)
    return next((v for v in data.values() if normalize_name(v["name"]) == target), None)


def suggested_languages(neighbourhood_name: str, hood_id: str | None = None) -> list[dict]:
    """Languages to suggest: the top translatable mother tongues spoken by ≥ 3% of residents
    (2021 Census, all 158 neighbourhoods). Falls back to the hand-entered list."""
    entry = _census_entry(neighbourhood_name, hood_id)
    if entry is None:
        return load_neighbourhood_languages().get(normalize_name(neighbourhood_name), [])
    out, seen = [], set()
    for l in entry["languages"]:
        if l["code"] and l["code"] not in seen and l["share"] >= SUGGEST_MIN_SHARE:
            seen.add(l["code"])
            out.append({"language": l["language"], "code": l["code"], "share": l["share"]})
        if len(out) == SUGGEST_MAX:
            break
    return out


def other_languages(neighbourhood_name: str, hood_id: str | None = None) -> list[dict]:
    """Common mother tongues NeighbourBrief can't translate into yet (≥ 2% of residents)."""
    entry = _census_entry(neighbourhood_name, hood_id)
    if entry is None:
        return []
    return [{"language": l["language"], "share": l["share"]} for l in entry["languages"]
            if not l["code"] and l["share"] >= ALSO_MIN_SHARE]
