"""Citizen Fraud Shield: instant risk triage for suspicious calls/messages.

Layered on the digital-arrest classifier plus heuristics for the common Indian
fraud families (UPI collect, OTP theft, KYC expiry, parcel, lottery, job,
investment). Advisories are template-based in English + 12 regional languages
(deterministic, offline — no translation API), with the language auto-detected
from the message script when not specified. IVR-length variants are included
so the same verdict can be read out over a voice menu.
"""
import re
from dataclasses import dataclass, field

from app.services.scam_detection_service import ScamDetector

FRAUD_TYPES: list[tuple[str, float, list[str]]] = [
    # (type, base_risk, patterns)
    ("UPI_COLLECT_FRAUD", 0.65, [
        r"collect request", r"upi pin.{0,30}(receive|refund|cashback)",
        r"scan (this |the )?qr.{0,30}(receive|get|refund)", r"pay ?tm.{0,20}kyc",
        r"enter (your )?(upi )?pin to receive",
    ]),
    ("OTP_THEFT", 0.7, [
        r"share (the |your )?otp", r"otp.{0,30}(send|tell|forward|share)",
        r"one.?time password.{0,30}(verify|confirm)",
        r"otp.{0,20}(शेयर|भेज|बता)", r"otp.{0,20}(share|শেয়ার|பகிர)",
    ]),
    ("PHISHING_LINK", 0.55, [
        r"(click|open).{0,30}(link|url)", r"bit\.ly|tinyurl|t\.co/", r"http[s]?://.{0,40}(kyc|refund|prize|verify)",
        r"account.{0,30}(suspended|blocked).{0,40}(click|link|verify)",
        r"लिंक.{0,30}(क्लिक|खोल)", r"(क्लिक|click).{0,15}(करके|करें|कर के)",
    ]),
    ("KYC_EXPIRY", 0.6, [
        r"kyc.{0,30}(expir|pending|update|suspend)", r"account (will be )?(blocked|frozen|suspended).{0,30}(today|24 hours|immediately)",
        r"kyc.{0,30}(समाप्त|अपडेट|बंद)", r"खाता.{0,30}(बंद|ब्लॉक|फ्रीज)",
    ]),
    ("UTILITY_DISCONNECT", 0.6, [
        r"electricity (bill|connection).{0,50}(disconnect|cut|overdue)",
        r"(power|electricity).{0,30}(will be )?(disconnected|cut).{0,30}(tonight|today|immediately)",
        r"बिजली.{0,30}(बिल|कनेक्शन).{0,50}(कट|काट|बकाया)",
        r"कनेक्शन.{0,20}(कट|काट)",
    ]),
    ("LOTTERY_PRIZE", 0.6, [
        r"(won|winner).{0,40}(lottery|lucky draw|prize|kbc)", r"claim (your )?(prize|reward|winning)",
        r"processing fee.{0,30}(prize|winning|lottery)",
    ]),
    ("PARCEL_CUSTOMS", 0.6, [
        r"parcel.{0,40}(customs|seized|held|drugs)", r"courier.{0,40}(illegal|suspicious|blocked)",
    ]),
    ("JOB_FRAUD", 0.5, [
        r"(work from home|part.?time job).{0,50}(earn|₹|rs)", r"registration fee.{0,30}(job|task|joining)",
        r"telegram.{0,40}(task|earning|job)",
    ]),
    ("INVESTMENT_FRAUD", 0.55, [
        r"guaranteed (returns?|profit)", r"double (your )?(money|investment)",
        r"trading (tips|signals).{0,30}(telegram|whatsapp|group)", r"crypto.{0,30}(scheme|plan|guaranteed)",
    ]),
    ("ARMY_OLX_FRAUD", 0.55, [
        r"army (officer|jawan|personnel).{0,60}(sell|buy|advance|posted)", r"cantonment.{0,40}(payment|advance)",
        r"paying (extra|advance).{0,30}(delivery|courier).{0,30}(army|posting)",
    ]),
]

HELPLINE = "1930"
REPORT_URL = "https://cybercrime.gov.in"

# Legitimate safety advisories ("never share your OTP") must not trip the
# OTP-theft family — a citizen-facing tool lives or dies on its FP rate.
_NEGATED_OTP_RE = re.compile(
    r"(never|do not|don'?t|कभी|मत|न)\s+\S{0,15}\s*(share|शेयर|बता|भेज)"
    r"|(share|शेयर|बता|भेज)\w*\s+(न करें|मत करें|नहीं)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Advisory templates: English + 12 regional languages. Keys are ISO 639-1.
# Each entry: high / medium / safe verdict lines + universal action lines.
# ---------------------------------------------------------------------------
ADVISORIES: dict[str, dict[str, str]] = {
    "en": {
        "name": "English",
        "high": "⚠ This is very likely a fraud attempt. Do not pay, share OTPs, or stay on the call.",
        "medium": "This message shows fraud warning signs. Verify independently through official numbers before acting.",
        "safe": "No strong fraud indicators found. Remain careful with payments and personal details.",
        "actions": "Hang up / do not reply · Never share OTP or PIN · Call helpline 1930 · Report at cybercrime.gov.in",
    },
    "hi": {
        "name": "हिन्दी",
        "high": "⚠ यह लगभग निश्चित रूप से धोखाधड़ी है। भुगतान न करें, OTP साझा न करें, कॉल पर न रहें।",
        "medium": "इस संदेश में धोखाधड़ी के संकेत हैं। कोई भी कदम उठाने से पहले आधिकारिक नंबर से पुष्टि करें।",
        "safe": "धोखाधड़ी के प्रबल संकेत नहीं मिले। फिर भी भुगतान और निजी जानकारी में सावधानी रखें।",
        "actions": "कॉल काटें · OTP/PIN कभी साझा न करें · हेल्पलाइन 1930 पर कॉल करें · cybercrime.gov.in पर रिपोर्ट करें",
    },
    "bn": {
        "name": "বাংলা",
        "high": "⚠ এটি প্রায় নিশ্চিতভাবে প্রতারণা। টাকা পাঠাবেন না, OTP দেবেন না, কলে থাকবেন না।",
        "medium": "এই বার্তায় প্রতারণার লক্ষণ আছে। পদক্ষেপের আগে সরকারি নম্বরে যাচাই করুন।",
        "safe": "প্রতারণার জোরালো লক্ষণ পাওয়া যায়নি। তবুও লেনদেনে সতর্ক থাকুন।",
        "actions": "কল কেটে দিন · OTP/PIN কখনও শেয়ার করবেন না · হেল্পলাইন 1930 · cybercrime.gov.in-এ রিপোর্ট করুন",
    },
    "ta": {
        "name": "தமிழ்",
        "high": "⚠ இது கிட்டத்தட்ட நிச்சயமாக மோசடி. பணம் அனுப்ப வேண்டாம், OTP பகிர வேண்டாம், அழைப்பில் தொடர வேண்டாம்.",
        "medium": "இந்தச் செய்தியில் மோசடி அறிகுறிகள் உள்ளன. அதிகாரப்பூர்வ எண்களில் சரிபார்க்கவும்.",
        "safe": "வலுவான மோசடி அறிகுறிகள் இல்லை. இருப்பினும் பரிவர்த்தனைகளில் எச்சரிக்கை தேவை.",
        "actions": "அழைப்பைத் துண்டிக்கவும் · OTP/PIN பகிர வேண்டாம் · உதவி எண் 1930 · cybercrime.gov.in-இல் புகார்",
    },
    "te": {
        "name": "తెలుగు",
        "high": "⚠ ఇది దాదాపు ఖచ్చితంగా మోసం. డబ్బు పంపవద్దు, OTP పంచుకోవద్దు, కాల్‌లో కొనసాగవద్దు.",
        "medium": "ఈ సందేశంలో మోసం సంకేతాలు ఉన్నాయి. అధికారిక నంబర్లతో ధృవీకరించండి.",
        "safe": "బలమైన మోసం సంకేతాలు కనిపించలేదు. అయినా లావాదేవీల్లో జాగ్రత్త వహించండి.",
        "actions": "కాల్ కట్ చేయండి · OTP/PIN పంచుకోవద్దు · హెల్ప్‌లైన్ 1930 · cybercrime.gov.in లో ఫిర్యాదు",
    },
    "mr": {
        "name": "मराठी",
        "high": "⚠ ही जवळपास नक्कीच फसवणूक आहे. पैसे पाठवू नका, OTP सांगू नका, कॉलवर राहू नका.",
        "medium": "या संदेशात फसवणुकीची लक्षणे आहेत. अधिकृत क्रमांकावरून खात्री करा.",
        "safe": "फसवणुकीची ठोस लक्षणे आढळली नाहीत. तरीही व्यवहारात सावध राहा.",
        "actions": "कॉल बंद करा · OTP/PIN कधीही सांगू नका · हेल्पलाइन 1930 · cybercrime.gov.in वर तक्रार करा",
    },
    "kn": {
        "name": "ಕನ್ನಡ",
        "high": "⚠ ಇದು ಬಹುತೇಕ ಖಚಿತವಾಗಿ ವಂಚನೆ. ಹಣ ಕಳುಹಿಸಬೇಡಿ, OTP ಹಂಚಿಕೊಳ್ಳಬೇಡಿ, ಕರೆಯಲ್ಲಿ ಮುಂದುವರಿಯಬೇಡಿ.",
        "medium": "ಈ ಸಂದೇಶದಲ್ಲಿ ವಂಚನೆಯ ಸೂಚನೆಗಳಿವೆ. ಅಧಿಕೃತ ಸಂಖ್ಯೆಗಳಿಂದ ಪರಿಶೀಲಿಸಿ.",
        "safe": "ಬಲವಾದ ವಂಚನೆ ಸೂಚನೆಗಳು ಕಂಡುಬಂದಿಲ್ಲ. ಆದರೂ ವಹಿವಾಟಿನಲ್ಲಿ ಎಚ್ಚರವಿರಲಿ.",
        "actions": "ಕರೆ ಕತ್ತರಿಸಿ · OTP/PIN ಹಂಚಬೇಡಿ · ಸಹಾಯವಾಣಿ 1930 · cybercrime.gov.in ನಲ್ಲಿ ದೂರು ನೀಡಿ",
    },
    "gu": {
        "name": "ગુજરાતી",
        "high": "⚠ આ લગભગ ચોક્કસ છેતરપિંડી છે. પૈસા ન મોકલો, OTP શેર ન કરો, કૉલ પર ન રહો.",
        "medium": "આ સંદેશમાં છેતરપિંડીના સંકેતો છે. સત્તાવાર નંબરોથી ખાતરી કરો.",
        "safe": "છેતરપિંડીના મજબૂત સંકેત મળ્યા નથી. છતાં વ્યવહારમાં સાવચેત રહો.",
        "actions": "કૉલ કાપો · OTP/PIN ક્યારેય શેર ન કરો · હેલ્પલાઇન 1930 · cybercrime.gov.in પર ફરિયાદ કરો",
    },
    "pa": {
        "name": "ਪੰਜਾਬੀ",
        "high": "⚠ ਇਹ ਲਗਭਗ ਯਕੀਨੀ ਧੋਖਾਧੜੀ ਹੈ। ਪੈਸੇ ਨਾ ਭੇਜੋ, OTP ਸਾਂਝਾ ਨਾ ਕਰੋ, ਕਾਲ 'ਤੇ ਨਾ ਰਹੋ।",
        "medium": "ਇਸ ਸੁਨੇਹੇ ਵਿੱਚ ਧੋਖਾਧੜੀ ਦੇ ਸੰਕੇਤ ਹਨ। ਸਰਕਾਰੀ ਨੰਬਰਾਂ ਤੋਂ ਪੁਸ਼ਟੀ ਕਰੋ।",
        "safe": "ਧੋਖਾਧੜੀ ਦੇ ਪੱਕੇ ਸੰਕੇਤ ਨਹੀਂ ਮਿਲੇ। ਫਿਰ ਵੀ ਲੈਣ-ਦੇਣ ਵਿੱਚ ਸਾਵਧਾਨ ਰਹੋ।",
        "actions": "ਕਾਲ ਕੱਟੋ · OTP/PIN ਕਦੇ ਸਾਂਝਾ ਨਾ ਕਰੋ · ਹੈਲਪਲਾਈਨ 1930 · cybercrime.gov.in 'ਤੇ ਸ਼ਿਕਾਇਤ ਕਰੋ",
    },
    "ml": {
        "name": "മലയാളം",
        "high": "⚠ ഇത് ഏതാണ്ട് ഉറപ്പായും തട്ടിപ്പാണ്. പണം അയയ്ക്കരുത്, OTP പങ്കിടരുത്, കോളിൽ തുടരരുത്.",
        "medium": "ഈ സന്ദേശത്തിൽ തട്ടിപ്പിന്റെ സൂചനകളുണ്ട്. ഔദ്യോഗിക നമ്പറുകളിൽ സ്ഥിരീകരിക്കുക.",
        "safe": "ശക്തമായ തട്ടിപ്പ് സൂചനകൾ കണ്ടെത്തിയില്ല. എന്നാലും ഇടപാടുകളിൽ ജാഗ്രത പാലിക്കുക.",
        "actions": "കോൾ വിച്ഛേദിക്കുക · OTP/PIN പങ്കിടരുത് · ഹെൽപ്പ്‌ലൈൻ 1930 · cybercrime.gov.in-ൽ പരാതി നൽകുക",
    },
    "or": {
        "name": "ଓଡ଼ିଆ",
        "high": "⚠ ଏହା ପ୍ରାୟ ନିଶ୍ଚିତ ଠକେଇ। ଟଙ୍କା ପଠାନ୍ତୁ ନାହିଁ, OTP ଦିଅନ୍ତୁ ନାହିଁ, କଲରେ ରୁହନ୍ତୁ ନାହିଁ।",
        "medium": "ଏହି ବାର୍ତ୍ତାରେ ଠକେଇର ସଙ୍କେତ ଅଛି। ସରକାରୀ ନମ୍ବରରୁ ଯାଞ୍ଚ କରନ୍ତୁ।",
        "safe": "ଠକେଇର ଦୃଢ଼ ସଙ୍କେତ ମିଳିଲା ନାହିଁ। ତଥାପି କାରବାରରେ ସତର୍କ ରୁହନ୍ତୁ।",
        "actions": "କଲ କାଟନ୍ତୁ · OTP/PIN କେବେ ଦିଅନ୍ତୁ ନାହିଁ · ହେଲ୍ପଲାଇନ 1930 · cybercrime.gov.in ରେ ଅଭିଯୋଗ କରନ୍ତୁ",
    },
    "as": {
        "name": "অসমীয়া",
        "high": "⚠ এইটো প্ৰায় নিশ্চিতভাৱে প্ৰতাৰণা। টকা নপঠিয়াব, OTP নিদিব, কলত নাথাকিব।",
        "medium": "এই বাৰ্তাত প্ৰতাৰণাৰ লক্ষণ আছে। চৰকাৰী নম্বৰৰ পৰা নিশ্চিত হওক।",
        "safe": "প্ৰতাৰণাৰ দৃঢ় লক্ষণ পোৱা নগ'ল। তথাপি লেনদেনত সাৱধান হওক।",
        "actions": "কল কাটক · OTP/PIN কেতিয়াও নিদিব · হেল্পলাইন 1930 · cybercrime.gov.in-ত অভিযোগ দিয়ক",
    },
    "ur": {
        "name": "اردو",
        "high": "⚠ یہ تقریباً یقینی طور پر دھوکہ دہی ہے۔ رقم نہ بھیجیں، OTP شیئر نہ کریں، کال پر نہ رہیں۔",
        "medium": "اس پیغام میں دھوکہ دہی کی علامات ہیں۔ سرکاری نمبروں سے تصدیق کریں۔",
        "safe": "دھوکہ دہی کے واضح آثار نہیں ملے۔ پھر بھی لین دین میں محتاط رہیں۔",
        "actions": "کال کاٹ دیں · OTP/PIN کبھی شیئر نہ کریں · ہیلپ لائن 1930 · cybercrime.gov.in پر رپورٹ کریں",
    },
}

# Unicode block → language guess (Devanagari defaults to Hindi; explicit
# lang param wins for Marathi. Assamese shares the Bengali block.)
_SCRIPT_RANGES: list[tuple[range, str]] = [
    (range(0x0900, 0x0980), "hi"),
    (range(0x0980, 0x0A00), "bn"),
    (range(0x0A00, 0x0A80), "pa"),
    (range(0x0A80, 0x0B00), "gu"),
    (range(0x0B00, 0x0B80), "or"),
    (range(0x0B80, 0x0C00), "ta"),
    (range(0x0C00, 0x0C80), "te"),
    (range(0x0C80, 0x0D00), "kn"),
    (range(0x0D00, 0x0D80), "ml"),
    (range(0x0600, 0x0700), "ur"),
]


@dataclass
class ShieldAssessment:
    verdict: str  # HIGH_RISK / SUSPICIOUS / LIKELY_SAFE
    risk_score: float
    fraud_type: str | None
    indicators: list[str] = field(default_factory=list)
    lang: str = "en"
    advisory: str = ""
    actions: str = ""
    ivr_text: str = ""


class FraudShield:
    def __init__(self) -> None:
        self._scam = ScamDetector()

    @staticmethod
    def detect_language(message: str) -> str:
        counts: dict[str, int] = {}
        for ch in message:
            cp = ord(ch)
            for block, lang in _SCRIPT_RANGES:
                if cp in block:
                    counts[lang] = counts.get(lang, 0) + 1
                    break
        return max(counts, key=counts.get) if counts else "en"

    def assess(self, message: str, *, caller_number: str | None = None,
               channel: str = "WEB", lang: str | None = None) -> ShieldAssessment:
        text = (message or "").lower()
        language = lang if lang in ADVISORIES else self.detect_language(message)

        # Digital-arrest classifier runs first — it is the highest-stakes family.
        scam = self._scam.assess(message, caller_number=caller_number, channel=channel)
        best_type: str | None = "DIGITAL_ARREST" if scam.risk_score >= 0.4 else None
        best_score = scam.risk_score
        indicators = list(scam.indicators[:4]) + list(scam.spoof_flags[:2])

        for fraud_type, base_risk, patterns in FRAUD_TYPES:
            hits = [re.search(p, text).group(0)[:60] for p in patterns if re.search(p, text)]
            if not hits:
                continue
            if fraud_type == "OTP_THEFT" and _NEGATED_OTP_RE.search(text):
                continue  # "never share your OTP" advisories are not requests
            score = min(base_risk + 0.1 * (len(hits) - 1), 0.95)
            if score > best_score:
                best_score, best_type = score, fraud_type
                indicators = [f"{fraud_type}: “{h}”" for h in hits[:4]]

        best_score = round(min(best_score, 1.0), 3)
        if best_score >= 0.6:
            verdict, level = "HIGH_RISK", "high"
        elif best_score >= 0.4:
            verdict, level = "SUSPICIOUS", "medium"
        else:
            verdict, level = "LIKELY_SAFE", "safe"

        pack = ADVISORIES[language]
        advisory, actions = pack[level], pack["actions"]
        ivr = f"{advisory} {pack['actions'].split('·')[2].strip()}"[:200]

        return ShieldAssessment(
            verdict=verdict,
            risk_score=best_score,
            fraud_type=best_type,
            indicators=indicators,
            lang=language,
            advisory=advisory,
            actions=actions,
            ivr_text=ivr,
        )

    @staticmethod
    def languages() -> list[dict]:
        return [{"code": code, "name": pack["name"]} for code, pack in ADVISORIES.items()]
