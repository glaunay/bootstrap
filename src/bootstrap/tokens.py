"""Estimation de la taille (en tokens) du contexte envoyé au LLM.

- tiktoken quand on connaît l'encodage du modèle, sinon ~4 caractères par token ;
- calibration : après chaque appel réussi, on compare l'estimation au nombre exact
  renvoyé par l'API (usage.prompt_tokens) et on corrige les estimations suivantes ;
- résistant au hors-ligne : si l'encodage ne peut pas être téléchargé, on retombe
  sur l'estimation par caractères au lieu de planter.
"""
import json
import os
from functools import lru_cache
from pathlib import Path

# tiktoken télécharge le fichier d'encodage au premier usage et le met en cache.
# Par défaut ce cache est dans le dossier temporaire (effacé régulièrement) : on le
# place dans un dossier durable, sauf si l'utilisateur a déjà choisi le sien.
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path.home() / ".cache" / "bootstrap" / "tiktoken"))

# Préfixe du nom de modèle -> encodage tiktoken. Modèle inconnu = pas de tiktoken.
MODEL_ENCODINGS = {
    "gpt-oss": "o200k_harmony",
    "gpt-4o": "o200k_base",
    "gpt-4.1": "o200k_base",
}

# Tokens de structure (rôle, séparateurs) ajoutés par le format de chat, par message.
PER_MESSAGE_OVERHEAD = 4

# Ratio exact / estimé, par modèle (moyenne glissante).
_calibration: dict[str, float] = {}
_ALPHA = 0.5   # poids de la nouvelle mesure dans la moyenne glissante


def encoding_name(model):
    for prefix, name in MODEL_ENCODINGS.items():
        if model.startswith(prefix):
            return name
    return None


@lru_cache(maxsize=None)
def _encoder(name):
    """Charge l'encodage une seule fois ; None si tiktoken ou le téléchargement échoue."""
    try:
        import tiktoken
        return tiktoken.get_encoding(name)
    except Exception:
        return None


def _texts(messages, tools):
    """Tous les morceaux de texte réellement envoyés : contenus, appels d'outils, schémas."""
    for m in messages:
        yield str(m.get("content") or "")
        for call in m.get("tool_calls") or []:
            yield call["function"]["name"]
            yield call["function"]["arguments"]
    if tools:
        yield json.dumps(tools, ensure_ascii=False)


def raw_count(messages, tools, model):
    """Estimation non calibrée -> (tokens, méthode)."""
    name = encoding_name(model)
    enc = _encoder(name) if name else None
    if enc is not None:
        n = sum(len(enc.encode(t, disallowed_special=())) for t in _texts(messages, tools))
        method = f"tiktoken:{name}"
    else:
        n = sum(len(t) for t in _texts(messages, tools)) // 4
        method = "chars/4"
    return n + PER_MESSAGE_OVERHEAD * len(messages), method


def count_tokens(messages, tools, model):
    """Estimation corrigée par la calibration -> (tokens, méthode)."""
    n, method = raw_count(messages, tools, model)
    ratio = _calibration.get(model)
    if ratio is None:
        return n, method
    return round(n * ratio), f"{method}, calibré x{ratio:.2f}"


def calibrate(model, raw_estimate, actual):
    """À appeler après un appel réussi, avec l'estimation brute de CETTE requête."""
    if not raw_estimate or not actual:
        return
    ratio = actual / raw_estimate
    old = _calibration.get(model)
    _calibration[model] = ratio if old is None else (1 - _ALPHA) * old + _ALPHA * ratio
