"""Подготовка русского текста для Silero: ручные исправления, числа, английские слова.

Silero не читает цифры и латиницу (молча пропускает их) и иногда ставит неверное ударение у слов-омографов.
Поэтому перед озвучкой текст проходит три шага:
  1. ручные исправления из stress_overrides.txt (ударения вида «дру+гом», произношение терминов),
  2. числа словами («50» -> «пятьдесят»),
  3. английские слова: аббревиатуры по буквам («IBM» -> «ай би эм»), остальные грубой транслитерацией.
Автоматические ударения расставляет сам Silero (put_accent=True), знак «+» в тексте он сохраняет.
"""
import re
from pathlib import Path

OVERRIDES_FILE = Path(__file__).parent / "stress_overrides.txt"

# ---------- ручные исправления ----------
_overrides_cache: tuple[float, list[tuple[re.Pattern, str]]] = (-1.0, [])


def _load_overrides() -> list[tuple[re.Pattern, str]]:
    """Читает stress_overrides.txt (строки «что=на что», # комментарий). Перечитывается при изменении файла."""
    global _overrides_cache
    try:
        mtime = OVERRIDES_FILE.stat().st_mtime
    except OSError:
        return []
    if mtime == _overrides_cache[0]:
        return _overrides_cache[1]
    rules = []
    for line in OVERRIDES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        src, dst = (p.strip() for p in line.split("=", 1))
        if src:
            rules.append((re.compile(r"(?<!\w)" + re.escape(src) + r"(?!\w)", re.IGNORECASE), dst))
    # длинные ключи первыми, чтобы «друг с другом» сработало раньше, чем «с другом»
    rules.sort(key=lambda r: -len(r[0].pattern))
    _overrides_cache = (mtime, rules)
    return rules


# ---------- числа ----------
_ONES = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять",
         "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
         "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_HUND = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]


def _triple(n: int, feminine: bool) -> list[str]:
    words = []
    if n >= 100:
        words.append(_HUND[n // 100])
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10])
        n %= 10
        if n:
            words.append(_ONES[n])
    elif n:
        words.append(_ONES[n])
    if feminine and words:
        if words[-1] == "один":
            words[-1] = "одна"
        elif words[-1] == "два":
            words[-1] = "две"
    return words


def _plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def int_to_words(n: int, gender: str = "m") -> str:
    """Число словами. gender: m/f/n, влияет на «один/одна/одно» и «два/две» в конце числа."""
    if n == 0:
        return "ноль"
    if n >= 10**12:
        return " ".join(_ONES[int(d)] if int(d) else "ноль" for d in str(n))  # слишком большое: по цифрам
    parts = []
    for scale, (one, few, many), fem in ((10**9, ("миллиард", "миллиарда", "миллиардов"), False),
                                         (10**6, ("миллион", "миллиона", "миллионов"), False),
                                         (10**3, ("тысяча", "тысячи", "тысяч"), True)):
        chunk, n = divmod(n, scale)
        if chunk:
            parts += _triple(chunk, fem) + [_plural(chunk, one, few, many)]
    if n:
        parts += _triple(n, gender == "f")
        if parts[-1] == "один" and gender == "n":
            parts[-1] = "одно"
    return " ".join(parts)


def _gender_of(word: str) -> str:
    """Грубая оценка рода существительного после числа: минута/минуты -> ж, окно -> ср, остальное -> м."""
    w = word.lower()
    if w.endswith(("ы", "и")):  # «две минуты», «две секунды» (но «один/два» + мужской род почти всегда на -а/-я/согласную)
        return "f"
    if w.endswith(("а", "я")):
        return "f"
    if w.endswith(("о", "е")):
        return "n"
    return "m"


def _numbers_to_words(text: str) -> str:
    def decimal(m: re.Match) -> str:
        whole, frac = m.group(1), m.group(2)
        tail = {1: "десятых", 2: "сотых", 3: "тысячных"}.get(len(frac))
        if not tail:  # длинную дробь читаем по цифрам
            return f"{int_to_words(int(whole))} точка " + " ".join(_ONES[int(d)] for d in frac)
        return f"{int_to_words(int(whole))} целых {int_to_words(int(frac))} {tail}"

    # «1 000 000» и «12 345»: пробел как разделитель тысяч
    text = re.sub(r"\b\d{1,3}(?:[  ]\d{3})+\b", lambda m: re.sub(r"[  ]", "", m.group(0)), text)
    text = re.sub(r"(\d+)[.,](\d+)", decimal, text)
    text = re.sub(
        r"(\d+)\s*%",
        lambda m: f"{int_to_words(int(m.group(1)))} {_plural(int(m.group(1)), 'процент', 'процента', 'процентов')}",
        text,
    )

    def with_noun(m: re.Match) -> str:
        n = int(m.group(1))
        noun = m.group(2)
        gender = _gender_of(noun) if noun and n % 100 not in range(11, 15) and n % 10 in (1, 2) else "m"
        # «2 минуты» -> «две минуты», «1 минута» -> «одна минута», но «2 файла» -> «два файла»:
        # у мужского рода форма «файла/часа» тоже на -а, поэтому для -а берём женский только при числе 1
        if noun and n % 10 == 2 and noun.lower().endswith(("а", "я")):
            gender = "m"
        return int_to_words(n, gender) + (" " + noun if noun else "")

    return re.sub(r"(\d+)(?:\s+([А-Яа-яЁё]+))?", with_noun, text)


# ---------- английские слова ----------
_LETTER_NAMES = {
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи", "h": "эйч", "i": "ай",
    "j": "джей", "k": "кей", "l": "эл", "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар",
    "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл ю", "x": "икс", "y": "уай", "z": "зед",
}
_DIGRAPHS = [("tion", "шн"), ("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"), ("ck", "к"), ("ee", "и"),
             ("oo", "у"), ("ou", "ау"), ("ea", "и"), ("qu", "кв"), ("kn", "н"), ("wh", "в"), ("ai", "эй"),
             ("ay", "эй"), ("oy", "ой"), ("er", "ер"), ("ing", "инг")]
_LETTERS = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж",
    "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т",
    "u": "у", "v": "в", "w": "в", "x": "кс", "y": "и", "z": "з",
}


def _translit(word: str) -> str:
    w = word.lower()
    out, i = [], 0
    while i < len(w):
        for src, dst in _DIGRAPHS:
            if w.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            ch = w[i]
            if ch == "c" and i + 1 < len(w) and w[i + 1] in "eiy":
                out.append("с")  # city, cyber
            else:
                out.append(_LETTERS.get(ch, ""))
            i += 1
    return "".join(out)


def _latin_to_russian(text: str) -> str:
    def repl(m: re.Match) -> str:
        word = m.group(0)
        if len(word) > 1 and word.isupper():  # аббревиатура: читаем по буквам
            return " ".join(_LETTER_NAMES[c.lower()] for c in word)
        return _translit(word)

    return re.sub(r"[A-Za-z]+", repl, text)


# ---------- итог ----------
def prepare_for_silero(text: str) -> str:
    for pattern, dst in _load_overrides():  # ключи ищутся в исходном тексте (там ещё есть «C++», латиница)
        text = pattern.sub(dst, text)
    # Знак ударения всегда стоит перед гласной («дру+гом»). Остальные «+» из исходного текста это просто плюс.
    text = re.sub(r"\+(?![аеёиоуыэюяАЕЁИОУЫЭЮЯ])", " плюс ", text)
    text = _numbers_to_words(text)
    text = _latin_to_russian(text)
    text = text.replace("&", " и ").replace("…", "...")
    # Silero не знает других символов: оставляем кириллицу, пробелы, простую пунктуацию и знак ударения
    text = re.sub(r"[^А-Яа-яЁё\s.,!?:;\-—+]", " ", text)
    text = re.sub(r"\s+([.,!?:;])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()
