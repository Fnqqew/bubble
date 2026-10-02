"""Frase de prueba de la voz en cada idioma («Probar voz»).

Cada voz se prueba en su propio idioma. Antes había frase para 18 idiomas y las demás voces leían la frase en inglés:
una voz que no conoce las letras latinas (hebreo, bengalí, georgiano…) devolvía casi silencio, y la prueba «sonaba»
sin que se escuchara nada.
"""

SAMPLES = {
    "es": "¡Hola! Así va a sonar mi voz.", "en": "Hi! This is how I'm going to sound.",
    "pt": "Oi! É assim que eu vou soar.", "fr": "Salut ! Voilà comment je vais sonner.",
    "de": "Hallo! So werde ich klingen.", "it": "Ciao! Ecco come suonerò.", "ru": "Привет! Вот так я буду звучать.",
    "tr": "Merhaba! Sesim böyle olacak.", "pl": "Cześć! Tak będę brzmieć.", "nl": "Hoi! Zo ga ik klinken.",
    "id": "Halo! Beginilah suaraku.", "tl": "Kumusta! Ganito ang magiging tunog ng boses ko.",
    "vi": "Xin chào! Giọng của tôi sẽ như thế này.", "th": "สวัสดี! เสียงของฉันจะเป็นแบบนี้",
    "ar": "مرحبا! هكذا سيبدو صوتي.", "ja": "こんにちは！こんな声になります。", "ko": "안녕하세요! 제 목소리는 이렇게 들려요.",
    "zh": "你好！我的声音听起来是这样的。", "hi": "नमस्ते! मेरी आवाज़ ऐसी सुनाई देगी।",
    "uk": "Привіт! Ось так звучатиме мій голос.", "sv": "Hej! Så här kommer min röst att låta.",
    "no": "Hei! Slik kommer stemmen min til å høres ut.", "da": "Hej! Sådan kommer min stemme til at lyde.",
    "fi": "Hei! Tältä ääneni kuulostaa.", "cs": "Ahoj! Takhle bude znít můj hlas.",
    "sk": "Ahoj! Takto bude znieť môj hlas.", "hu": "Szia! Így fog szólni a hangom.",
    "ro": "Salut! Așa va suna vocea mea.", "el": "Γεια! Έτσι θα ακούγεται η φωνή μου.",
    "bg": "Здравей! Така ще звучи гласът ми.", "sr": "Zdravo! Ovako će zvučati moj glas.",
    "hr": "Bok! Ovako će zvučati moj glas.", "sl": "Živjo! Tako bo zvenel moj glas.",
    "mk": "Здраво! Вака ќе звучи мојот глас.", "lt": "Labas! Taip skambės mano balsas.",
    "lv": "Sveiki! Lūk, kā skanēs mana balss.", "et": "Tere! Nii hakkab mu hääl kõlama.",
    "be": "Прывітанне! Вось так будзе гучаць мой голас.", "he": "שלום! ככה הקול שלי יישמע.",
    "fa": "سلام! صدای من این‌طوری شنیده می‌شود.", "ur": "ہیلو! میری آواز ایسی سنائی دے گی۔",
    "bn": "হ্যালো! আমার কণ্ঠ এমন শোনাবে।", "mr": "नमस्कार! माझा आवाज असा ऐकू येईल.",
    "te": "హలో! నా గొంతు ఇలా వినిపిస్తుంది.", "ta": "வணக்கம்! என் குரல் இப்படித்தான் ஒலிக்கும்.",
    "gu": "નમસ્તે! મારો અવાજ આવો સંભળાશે.", "pa": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੇਰੀ ਆਵਾਜ਼ ਇਸ ਤਰ੍ਹਾਂ ਸੁਣਾਈ ਦੇਵੇਗੀ।",
    "ms": "Helo! Beginilah bunyi suara saya.", "ka": "გამარჯობა! ჩემი ხმა ასე გაჟღერდება.",
    "hy": "Բարև! Իմ ձայնը այսպես կհնչի։", "kk": "Сәлем! Менің дауысым осылай естіледі.",
    "az": "Salam! Səsim belə eşidiləcək.", "ca": "Hola! Així sonarà la meva veu.",
    "eu": "Kaixo! Horrela entzungo da nire ahotsa.", "cy": "Helo! Fel hyn bydd fy llais yn swnio.",
    "is": "Halló! Svona mun röddin mín hljóma.", "sq": "Përshëndetje! Kështu do të tingëllojë zëri im.",
    "af": "Hallo! So gaan my stem klink.", "sw": "Habari! Hivi ndivyo sauti yangu itakavyosikika.",
}


def sample_for(language: str) -> str:
    """La frase de prueba en ese idioma ("es-AR" → la de "es"); en inglés si no hay."""
    return SAMPLES.get(language.split("-")[0].lower(), SAMPLES["en"])
