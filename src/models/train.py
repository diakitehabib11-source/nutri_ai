from importlib.resources import open_text
import os
import time
import json
import base64
import requests
from datetime import datetime, date
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS
import re
import shutil
from pathlib import Path
import sys
from openai import OpenAI

# Load environment variables
load_dotenv()

# Constants
MAX_CHARS = 20000
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
DB_NAME = os.getenv("DB_NAME", "visa2023")
DB_ZAMANUTRI_NAME = os.getenv("DB_NAME", "ziamanutri")

# HEYGEN 
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")

# OPEN AI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ELEVENLABS
ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY")
ELEVEN_VOICE_ID = os.getenv("ELEVEN_VOICE_ID")  # obligatoire pour ElevenLabs endpoint

# D-ID 
DID_API_KEY_FULL = os.getenv("DID_API_KEY_FULL")  # "username:password"
DID_USERNAME = os.getenv("DID_USERNAME")
DID_PASSWORD = os.getenv("DID_PASSWORD")

DID_SOURCE_IMAGE = os.getenv("DID_SOURCE_IMAGE")  # url publique d'une image (obligatoire)

OUTPUT_AUDIO = os.getenv("OUTPUT_AUDIO", "tts_output.mp3")
DID_API_KEY = os.getenv("DID_API_KEY", "Bearer TON_TOKEN_ICI")

# Image de l'avatar (URL d'une photo sur le web)
SOURCE_IMAGE = "https://create-images-results.d-id.com/example-avatar.jpg"

# Texte à transformer en vidéo
TEXT_INPUT = "Bonjour, je suis votre assistant virtuel. Voici une démonstration réaliste de la génération vidéo avec D-ID."

# Dossier de sortie
OUTPUT_FILE = "video_result.mp4"

# Poll interval & timeout
POLL_INTERVAL = 5
POLL_TIMEOUT = 180

# configuration du polling D-ID
DID_POLL_INTERVAL = 5  # secondes
DID_POLL_TIMEOUT = 180  # secondes (3 min)

# Vérifier qu'une variable d'environnement est définie
def require_env(varname, value):
    if not value:
        raise RuntimeError(f"Variable d'environnement manquante : {varname}")

# En-tête d'authentification de base pour D-ID
def build_did_basic_auth_header():
    if DID_API_KEY_FULL and ":" in DID_API_KEY_FULL:
        raw = DID_API_KEY_FULL
    elif DID_USERNAME and DID_PASSWORD:
        raw = f"{DID_USERNAME}:{DID_PASSWORD}"
    else:
        raise RuntimeError("D-ID credentials manquantes. Défini DID_API_KEY_FULL ou DID_USERNAME + DID_PASSWORD")
    token = base64.b64encode(raw.encode()).decode()
    return {"Authorization": f"Basic {token}"}

# endepoint du patient
PATIENT_API = "http://157.230.10.246:5000/api/v2/patients"
CONSULT_API = "http://157.230.10.246:5001/api/v1/consultations/last-by-patient/698607d0e0f96ed3c2817ae2"

def fetch_patient_and_consultations(patient_id_str):
    """
    Récupère les informations d'un patient et sa dernière consultation
    via endpoints REST.
    """

    if not patient_id_str:
        raise ValueError("Patient ID is required.")

    # ==========================
    # 1️⃣ Récupération du patient
    # ==========================
    try:
        patient_response = requests.get(
            f"{PATIENT_API}/{patient_id_str}",
            timeout=10
        )
        patient_response.raise_for_status()
        patient = patient_response.json()
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Erreur lors de la récupération du patient: {e}")

    if not patient:
        raise LookupError("No patient found for this ID.")

    # ==========================
    # 2️⃣ Récupération consultation
    # ==========================
    try:
        consult_response = requests.get(
            f"{CONSULT_API}/{patient_id_str}",
            timeout=10
        )
        consult_response.raise_for_status()
        consultations = consult_response.json()
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"Erreur lors de la récupération des consultations: {e}")

    return patient, consultations


#============================================================
'''# ETAPE 2: Récupération des informations du patient et de ses consultations depuis MongoDB

# Mongo : récupéreration des informations du patient et sa consultations
def fetch_patient_and_consultations(patient_id_str):
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    try:
        oid = ObjectId(patient_id_str)
    except Exception as e:
        raise ValueError(f"Invalid patient ID: {e}")

    patient = db.patients.find_one({"_id": oid})
    if not patient:
        raise LookupError("No patient found for this ID.")

    consultations = list(db.consultations.find({"patient_id": patient["_id"]}))
    return patient, consultations'''

#============================================================
# GEOLOCALISATION & DETECRION DE LA SAISON

def get_geolocation():
    response = requests.get("https://ipinfo.io/json", timeout=5)
    response.raise_for_status()
    return response.json()

def detect_season(geo):
    month = datetime.now().month
    country = geo.get("country")

    tropical_countries = ["BR", "IN", "ID", "TH", "PH", "NG", "MX", "GN"]

    # Détection de la saison en fonction du pays et la date du jour
    if country in tropical_countries:
        return "saison sèche" if month in [11,12,1,2,3,4] else "saison des pluies"

    if month in [12,1,2]:
        return "hiver"
    if month in [3,4,5]:
        return "printemps"
    if month in [6,7,8]:
        return "été"
    return "automne"

def get_fruits_by_season(season):
    return {
        "hiver": ["orange", "kiwi"],
        "printemps": ["fraise", "cerise"],
        "été": ["mangue", "pastèque"],
        "automne": ["raisin", "pomme"],
        "saison sèche": ["banane", "ananas"],
        "saison des pluies": ["goyave", "corossol"]
    }.get(season, [])



#===============================================================
# ETAPE 3: Génération du prompt pour OpenAI ou Groq
# Création d'un selecteur génération du prompt avec Groq et s'il y'a erreur OpenAI fait la génération du prompt
def build_prompt(patient, consultations, use_groq=False):
    if use_groq:
        # ETAPE 3.1 : Groq
        def build_groq_prompt_from_patient(patient, consultations):
            # assemble en français un contexte lisible pour l'IA
            lines = []
            full_name = f"{patient.get('first_name','')} {patient.get('last_name','')}".strip()
            lines.append(f"Dossier patient : {full_name}")
            lines.append(f"Sexe : {patient.get('gender','N/A')}")
            birth = patient.get('birthdate')
            age_str = "N/A"
            if birth:
                try:
                    if isinstance(birth, str):
                        bd = datetime.strptime(birth, "%Y-%m-%d").date()
                    elif isinstance(birth, datetime):
                        bd = birth.date()
                    else:
                        bd = birth
                    today = date.today()
                    age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
                    age_str = f"{age} ans"
                except Exception:
                    age_str = "N/A"
            lines.append(f"Âge : {age_str}")
            hist = patient.get('histoire_medicale') or patient.get('histoire_medicale', []) or patient.get('histoire', []) or []
            if isinstance(hist, list):
                lines.append("Historique médical : " + (", ".join(map(str, hist)) if hist else "Aucun"))
            else:
                lines.append("Historique médical : " + str(hist))
            lines.append("Consultations (diagnostics trouvés) :")
            if consultations:
                for c in consultations:
                    diag = c.get('diagnostique', c.get('diagnosis', 'N/A'))
                    lines.append(f" - {diag}")
            else:
                lines.append("Aucune consultation trouvée.")
            lines.append("\nEn vous basant sur les informations ci-dessus, rédigez un plan nutritionnel personnalisé pour le patient. "
                         "Le plan doit inclure des recommandations diététiques adaptées à son âge, sexe et historique médical. "
                         "Présentez le plan sous forme de paragraphe narratif, en utilisant un ton professionnel et empathique.")
            return "\n".join(lines)
        return build_groq_prompt_from_patient(patient, consultations)
    else:
        # ETAPE 3.2 : OpenAI
        def build_openai_prompt_from_patient(patient, consultations):
            lines = []
            full_name = f"{patient.get('first_name','')} {patient.get('last_name','')}".strip()
            lines.append(f"Dossier patient : {full_name}")
            lines.append(f"Sexe : {patient.get('gender','N/A')}")
            birth = patient.get('birthdate')
            age_str = "N/A"
            if birth:
                try:
                    if isinstance(birth, str):
                        bd = datetime.strptime(birth, "%Y-%m-%d").date()
                    elif isinstance(birth, datetime):
                        bd = birth.date()
                    else:
                        bd = birth
                    today = date.today()
                    age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
                    age_str = f"{age} ans"
                except Exception:
                    age_str = "N/A"
            lines.append(f"Âge : {age_str}")
            hist = patient.get('histoire_medicale') or patient.get('histoire_medicale', []) or patient.get('histoire', []) or []
            if isinstance(hist, list):
                lines.append("Historique médical : " + (", ".join(map(str, hist)) if hist else "Aucun"))
            else:
                lines.append("Historique médical : " + str(hist))
            lines.append("Consultations (diagnostics trouvés) :")
            if consultations:
                for c in consultations:
                    diag = c.get('diagnostique', c.get('diagnosis', 'N/A'))
                    lines.append(f" - {diag}")
            else:
                lines.append("Aucune consultation trouvée.")
            lines.append("\nEn vous basant sur les informations ci-dessus, rédigez un plan nutritionnel personnalisé pour le patient. "
                         "Le plan doit inclure des recommandations diététiques adaptées à son âge, sexe et historique médical. "
                         "Présentez le plan sous forme de paragraphe narratif, en utilisant un ton professionnel et empathique.")
            return "\n".join(lines)
        return build_openai_prompt_from_patient(patient, consultations) 
def build_prompt_from_patient(patient, consultations):
    try:
        return build_prompt(patient, consultations, use_groq=True)
    except Exception as e:
        print(f"[WARN] Groq prompt generation failed: {e}. Falling back to OpenAI.")
        return build_prompt(patient, consultations, use_groq=False) 
# Appel OpenAI Chat API
def call_openai_chat(prompt):
    require_env("OPENAI_API_KEY", OPENAI_API_KEY)
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "model": "gpt-4",
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    r = requests.post(url, headers=headers, json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


#===============================================================
# ETAPE 4: Génération audio avec ElevenLabs

# ElevenLabs : Text-to-Speech (sauvegarde locale)
# Docs : endpoint POST /v1/text-to-speech/{voice_id}

def elevenlabs_tts_to_file(text, out_file=OUTPUT_AUDIO):
    require_env("ELEVEN_API_KEY", ELEVEN_API_KEY)
    require_env("ELEVEN_VOICE_ID", ELEVEN_VOICE_ID)

    # ElevenLabs may have character limits; si trop long on peut couper (ici on tronque si >20000)
    MAX_CHARS = 20000
    if len(text) > MAX_CHARS:
        print(f"[WARN] Texte trop long pour ElevenLabs ({len(text)} chars). Il sera tronqué.")
        text = text[:MAX_CHARS]

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "text": text,
        "model_id": "eleven_multilingual_v2"  # recommandé par la doc
    }
    r = requests.post(url, headers=headers, json=body, stream=True, timeout=120)
    r.raise_for_status()
    with open(out_file, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    print(f"[OK] Audio TTS sauvegardé : {out_file}")
    return out_file



#===============================================================
# ETAPE 5: Upload audio vers D-ID
# Docs : endpoint POST /audios

'''def build_auth_header():
    if ":" in DID_API_KEY and not DID_API_KEY.lower().startswith(("bearer", "basic")):
        token = base64.b64encode(DID_API_KEY.encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {"Authorization": DID_API_KEY}


def create_talk(text, source_image):
    url = "https://api.d-id.com/talks"
    headers = build_auth_header()
    headers.update({"Content-Type": "application/json"})

    payload = {
        "source_url": source_image,
        "script": {
            "type": "text",
            "input": text,
            "provider": {"type": "microsoft", "voice_id": "fr-FR-DeniseNeural"}
        },
        "config": {"stitch": True},
        "visibility": "public"
    }

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    talk_id = data.get("id")
    if not talk_id:
        raise RuntimeError(f"Aucune ID renvoyée par D-ID : {data}")
    print(f"[OK] Talk créé id={talk_id}")
    return talk_id


def poll_talk_and_download(talk_id, output_file):
    url = f"https://api.d-id.com/talks/{talk_id}"
    headers = build_auth_header()
    start = time.time()

    while True:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status in ("done", "completed", "finished"):
            video_url = data.get("result", {}).get("url") or data.get("video_url")
            if not video_url:
                raise RuntimeError("Pas d'URL vidéo dans la réponse.")
            print(f"[OK] Vidéo prête : {video_url}")

            # Télécharger le fichier vidéo
            resp = requests.get(video_url, stream=True, timeout=120)
            resp.raise_for_status()
            with open(output_file, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            print(f"[FIN] Vidéo téléchargée : {output_file}")
            return output_file

        elif status in ("error", "failed"):
            raise RuntimeError(f"Erreur D-ID : {data}")

        elif time.time() - start > POLL_TIMEOUT:
            raise TimeoutError(f"Timeout après {POLL_TIMEOUT}s")

        print(f"[INFO] Statut={status}, nouvelle vérif dans {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)

def poll_did_talk(talk_id):
    url = f"https://api.d-id.com/talks/{talk_id}"
    headers = build_auth_header()
    start = time.time()

    while True:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status in ("done", "completed", "finished"):
            return data
        elif status in ("error", "failed"):
            raise RuntimeError(f"Erreur D-ID : {data}")
        elif time.time() - start > POLL_TIMEOUT:
            raise TimeoutError(f"Timeout après {POLL_TIMEOUT}s")
        print(f"[INFO] Statut={status}, nouvelle vérif dans {POLL_INTERVAL}s...")
        time.sleep(POLL_INTERVAL)

def create_did_talk_using_audio(audio_info, source_image):
    url = "https://api.d-id.com/talks"
    headers = build_auth_header()
    headers.update({"Content-Type": "application/json"})

    payload = {
        "source_url": source_image,
        "script": {
            "type": "audio",
            "input": audio_info["url"]
        },
        "config": {"stitch": True},
        "visibility": "public"
    }

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    talk_id = data.get("id")
    if not talk_id:
        raise RuntimeError(f"Aucune ID renvoyée par D-ID : {data}")
    print(f"[OK] Talk créé id={talk_id}")
    return talk_id
def upload_audio_to_did(audio_path):
    url = "https://api.d-id.com/audios"
    headers = build_auth_header()
    files = {"file": open(audio_path, "rb")}

    r = requests.post(url, headers=headers, files=files, timeout=60)
    r.raise_for_status()
    data = r.json()
    audio_url = data.get("url")
    if not audio_url:
        raise RuntimeError(f"Aucune URL audio renvoyée par D-ID : {data}")
    print(f"[OK] Audio uploadé vers D-ID : {audio_url}")
    return {"url": audio_url}'''



#===============================================================
# Étape 6: génération avec OpenAI des images des fruits et plats qui n'existe pas dans notre base deonnées 

# === LISTE DE FRUITS (PROMPTS) ===
'''fruit_prompts = {
    "pomme":"une pomme rouge réaliste sur fond transparent",
    "poivron": "un poivron découpé en tranche, réaliste sur un fond transparent",
    "poire" : "une poire verte réaliste sur fond transparent",
    "orange": "une orange fraîche réaliste sur fond transparent",
    "clémentine": "une clémentine pelée avec segments visibles sur fond transparent",
    "fraise": "un bol de fraises fraîches réalistes sur fond transparent",
    "framboises": "des framboises réalistes sur fond transparent",
    "myrtilles": "une poignée de myrtilles réalistes sur fond transparent",
    "kiwi": "un kiwi coupé en deux réaliste sur fond transparent",
    "prune": "une prune violette réaliste sur fond transparent",
    "pêche": "une pêche réaliste sur fond transparent",
    "cerises": "des cerises avec tiges réalistes sur fond transparent",
    "pamplemousse": "un pamplemousse coupé en deux réaliste sur fond transparent",
    "grenade": "une grenade ouverte avec grains visibles réaliste sur fond transparent",
    "banane": "une petite banane réaliste sur fond transparent",
    "raisins": "une grappe de raisins réalistes sur fond transparent",
    "mangue": "une mangue réaliste sur fond transparent",
    "ananas": "une tranche d’ananas réaliste sur fond transparent",
    "citron": "une tranche de citron réaliste sur fond transparent",
    "ail": "une gousse d'ail, réaliste sur fond transparent",
    "gingimbre":"une gousse de gingimbre de découpée en tranches",
    "date": "une poignée de date, réaliste sur fond transparent",
    "mangues": "une assiète de mangues decoupées en petites tranches",
    "goyave": "une trance de goyave, réaliste sur fond transparent",
    "avocats": "un d'avocats coupés en deux tranches, réaliste sur fond transparent",
    "tomates": "une tomate decoupée en deux tranches, réaliste sur fond transparent",
    "miel": "un demi verre de miel, réaliste sur fond transparent",
    "lait": "un verre de lait, réaliste sur fond transparent",
    "papaye": "une papaye ouverte réaliste sur fond transparent"
}


    # === LISTE DE PLATS (PROMPTS) ===
plat_prompts = {
    "plat de légumes": "un plat de légumes vapeur colorés (brocolis, carottes, haricots verts) sur fond transparent",
    "poulet": "un filet de poulet grillé avec des herbes, réaliste sur fond transparent",
    "poisson": "un poisson grillé (saumon) avec des rondelles de citron, réaliste sur fond transparent",
    "salade": "une salade composée avec avocat, tomate, concombre et poulet grillé, réaliste sur fond transparent",
    "quinoa": "un bol de quinoa avec légumes colorés, sur fond transparent",
    "légumes": "une soupe de légumes diététique servie dans un bol, sur fond transparent",
    "riz": "un plat de riz complet avec légumes sautés, réaliste sur fond transparent",
    "riz au poulet": "un plat du riz blanc complet avec polets sauté, sur fond transparent",
    "riz au poisson": "un plat du riz blanc complet avec poisson sautés; sur fond transparent",
    "yaourt": "un yaourt nature, réaliste sur fond transparent",
    "yaourt au fruit": "un yaourt nature avec fruits rouges frais, réaliste sur fond transparent",
    "smoothie": "un smoothie vert (épinard, kiwi, pomme, concombre), réaliste sur fond transparent",
    "omelette": "une omelette aux légumes (poivrons, champignons, tomates), réaliste sur fond transparent",
    "lentille": "un plat de lentilles mijotées avec carottes et céleri, réaliste sur fond transparent",
    "wrap": "un wrap de poulet grillé avec légumes frais, réaliste sur fond transparent",
    "salade de pois": "un bol de salade de pois chiches avec concombre et tomates, réaliste sur fond transparent",
    "crudité": "un plateau de crudités (carottes, concombres, céleri) avec houmous, réaliste sur fond transparent",
    "potiron": "un bol de soupe de potiron, réaliste sur fond transparent",
    "carotte": "un bol de carotte decoupées en tranche, réaliste sur fond transparent"
}

# === PROMPT POUR PLUSIEURS IMAGES ===
# Vérifier si des aliments/plats du texte existent dans notre base de données mongo ZiamaNutri, sinon générer des images via OpenAI Images API

# ===  RECHERCHE DES ALIMENTS DANS fruit_prompts et plat_prompts ===
def extraire_contenus_multi(paragraphe: str, *dicts)-> list:
    trouvés=[]
    texte= paragraphe.lower()

    for d in dicts:
        for cle, valeur in d.items():
            if cle.lower() in texte:    
                trouvés.append(valeur)

    return trouvés
# Mettre les aliments/plats trouvés dans une liste sans doublons
def extraire_aliments_plats(paragraphe: str) -> list:
    resultat = extraire_contenus_multi(paragraphe, fruit_prompts, plat_prompts)
    # supprimer les doublons en gardant l'ordre
    seen = set()
    aliments_plats = []
    for r in resultat:
        if r not in seen:
            seen.add(r)
            aliments_plats.append(r)
    return aliments_plats

# == Recherche des aliments/plats trouvés dans mongnoDB ZiamaNutri ==

def check_foods_in_db(aliments_plats: list) -> set:
    client = MongoClient(MONGO_URL)
    db = client["ZiamaNutri"]
    found_foods = set()

    for food in aliments_plats:
        # Vérifier dans la collection 'fruits'
        fruit = db.fruits.find_one({"name": {"$regex": f"^{food}$", "$options": "i"}})
        if fruit:
            found_foods.add(food)
            continue

        # Vérifier dans la collection 'plats'
        plat = db.plats.find_one({"name": {"$regex": f"^{food}$", "$options": "i"}})
        if plat:
            found_foods.add(food)

    client.close()
    return found_foods


def generate_images_from_text(paragraphe: str) -> list:
    """Extrait les aliments/plats du texte et génère des images via l'API Images OpenAI.
    Retourne la liste des fichiers sauvegardés."""
    require_env("OPENAI_API_KEY", OPENAI_API_KEY)
    resultat = extraire_contenus_multi(paragraphe, fruit_prompts, plat_prompts)
    if not resultat:
        print("[INFO] Aucun aliment/plats détectés pour générer des images.")
        return []

    # supprimer les doublons en gardant l'ordre
    seen = set()
    prompt_texts = []
    for r in resultat:
        if r not in seen:
            seen.add(r)
            prompt_texts.append(r)

    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    saved_files = []
    for i, prompt in enumerate(prompt_texts, start=1):
        payload = {
            "model": "dall-e-3",
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "response_format": "b64_json"
        }
        r = requests.post("https://api.openai.com/v1/images/generations", headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        b64 = data["data"][0].get("b64_json") or data["data"][0].get("b64")
        image_bytes = base64.b64decode(b64)
        filename = f"image_{i}.png"
        with open(filename, "wb") as f:
            f.write(image_bytes)
        print(f"[OK] Image sauvegardée : {filename}")
        saved_files.append(filename)

    return saved_files

# Ajouter les images générées dans la base données MongoDB ZiamaNutri
def add_images_to_db(image_files: list, paragraphe: str):
    client = MongoClient(MONGO_URL)
    db = client["ZiamaNutri"]

    for image_file in image_files:
        with open(image_file, "rb") as f:
            image_data = f.read()
        # Trouver le nom de l'aliment/plat correspondant au prompt
        for nom, prompt in {**fruit_prompts, **plat_prompts}.items():
            if prompt in paragraphe:
                # Insérer dans la collection appropriée
                collection_name = "fruits" if nom in fruit_prompts else "plats"
                db[collection_name].insert_one({
                    "name": nom,
                    "image_data": image_data
                })
                print(f"[OK] Image de '{nom}' ajoutée à la collection '{collection_name}'")
                break

    client.close()

# Ajouter les images généreées et celle recupérées de la base de données MongoDB ZiamaNutri dans D-ID et en faire une vidéo finale
def process_and_store_images(paragraphe: str):
    aliments_plats = extraire_aliments_plats(paragraphe)
    if not aliments_plats:
        print("[INFO] Aucun aliment/plat détecté dans le texte.")
        return

    found_foods = check_foods_in_db(aliments_plats)
    missing_foods = set(aliments_plats) - found_foods

    print(f"[INFO] Aliments/plats trouvés dans la base de données : {found_foods}")
    print(f"[INFO] Aliments/plats manquants à générer : {missing_foods}")

    if missing_foods:
        image_files = generate_images_from_text(paragraphe)
        add_images_to_db(image_files, paragraphe)
        return image_files
    return []

# Télécharger la vidéo finale avec les images des aliments/plats
def download_final_video(video_url: str, output_path: str):
    resp = requests.get(video_url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    print(f"[FIN] Vidéo finale téléchargée : {output_path}")
    return output_path

# Génération de la vidéo
# == 13. création du prompte directrice pour la vidéo ==
def create_video_prompt(narrative_text, generated_images, local_images):
    prompt = "Créer une vidéo courte (10 à 30 secondes) en utilisant le texte narratif suivant et les images fournies.\n\n"
    prompt += "Texte narratif :\n"
    prompt += narrative_text + "\n\n"
    prompt += "Images générées :\n"
    for desc, url in generated_images:
        prompt += f"- {desc}: {url}\n"
    prompt += "Images locales :\n"
    for name, path in local_images:
        prompt += f"- {name}: {path}\n"
    prompt += "\nInstructions pour la vidéo :\n"
    prompt += (
        "1. Utiliser un ton professionnel et empathique.\n"
        "2. Présenter le menu de manière claire et appétissante.\n"
        "3. Ajouter des transitions douces entre les images.\n"
        "4. Inclure une musique de fond apaisante.\n"
        "5. Terminer par un message encourageant pour le patient.\n"
        "6. Synchroniser le texte narratif avec les images affichées.\n"
        "7. Assurer une bonne qualité visuelle et sonore.\n"
        "8. Afficher l'image de chaque aliment ou plat lorsqu'il est mentionné dans le texte.\n"
        "9. Exporter la vidéo au format MP4.\n"
    )
    return prompt
def save_to_file(content, filename):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[OK] Contenu sauvegardé dans le fichier : {filename}")
# Exemple d'utilisation
narrative_text = open_text  # Texte narratif généré précédemment
generated_images = [("pomme rouge réaliste", "https://example.com/image1.png"),
                    ("salade composée avec avocat", "https://example.com/image2.png")]
local_images = [("banane réaliste", "image_3.png"),
                ("poulet grillé avec des herbes", "image_4.png")]

video_prompt = create_video_prompt(narrative_text, generated_images, local_images)
print("Video Creation Prompt:\n", video_prompt)
save_to_file(video_prompt, "video_prompt.txt")

# Génération de la vidéo avec OpenAI tout en se servant du prompte directrice 
def main_video_generation():
    openai = OpenAI()

    video = openai.videos.create(
        model="sora-2",
        prompt=video_prompt,
    )

    print("Video generation started:", video)

    progress = getattr(video, "progress", 0)
    bar_length = 30

    while video.status in ("in_progress", "queued"):
        # Refresh status
        video = openai.videos.retrieve(video.id)
        progress = getattr(video, "progress", 0)

        filled_length = int((progress / 100) * bar_length)
        bar = "=" * filled_length + "-" * (bar_length - filled_length)
        status_text = "Queued" if video.status == "queued" else "Processing"

        sys.stdout.write(f"\r{status_text} : [{bar}] {progress:.1f}%")
        sys.stdout.flush()
        time.sleep(2)

    # Move to next line after progress loop
    sys.stdout.write("\n")

    if video.status == "failed":
        message = getattr(
            getattr(video, "error", None), "message", "Video generation failed"
        )
        print(message)
        return

    print("Video generation completed:", video)
    print("Downloading video content...")

    content = openai.videos.download_content(video.id, variant="video")
    content.write_to_file("video.mp4")

    print("Wrote video.mp4")'''
    
#===============================================================
# Main pipeline

def main():
    # Check required environment variables
    require_env("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"))
    require_env("ELEVEN_API_KEY", os.getenv("ELEVEN_API_KEY"))
    require_env("ELEVEN_VOICE_ID", os.getenv("ELEVEN_VOICE_ID"))
    require_env("DID_SOURCE_IMAGE", os.getenv("DID_SOURCE_IMAGE"))
    require_env("HEYGEN_API_KEY", os.getenv("HEYGEN_API_KEY"))

    patient_id = input("Enter patient ID (ObjectId 24 chars): ").strip()
    try:
        patient, consultations = fetch_patient_and_consultations(patient_id)
    except Exception as e:
        print(f"[ERROR] Failed to retrieve patient: {e}")
        return

    print("Affichage de la Géolocalisation et la saison")
    try:
        geo = get_geolocation()
        season = detect_season(geo)
        fruits = get_fruits_by_season(season)
        # Affichage compact
        print("La zone géographique est :", geo.get("country", geo))
        print("La saison est :", season)
        print("Les fruits disponibles :", ", ".join(fruits) if fruits else "Aucun")
    except Exception as e:
        print(f"[WARN] Impossible de récupérer la géolocalisation / saison : {e}")
     
    print("[INFO] Patient et consultations récupérés — génération du prompt pour OpenAI...")
    prompt = build_prompt_from_patient(patient, consultations)

    try:
        openai_text = call_openai_chat(prompt)
        print("[OK] Réponse OpenAI reçue :\n", openai_text)
    except Exception as e:
        print(f"[ERR] appel OpenAI échoué : {e}")
        return

    # Génération audio via ElevenLabs
    '''try:
        audio_path = elevenlabs_tts_to_file(openai_text, OUTPUT_AUDIO)
    except Exception as e:
        print(f"[ERR] TTS ElevenLabs échoué : {e}")
        return'''

    # Upload audio à D-ID (effectué lors de la création du talk)
    # (la création du talk ci-dessous effectue l'upload si nécessaire)

    # Create talk
    '''try:
        audio_info = upload_audio_to_did(audio_path)
        talk_id = create_did_talk_using_audio(audio_info, DID_SOURCE_IMAGE)
    except Exception as e:
        print(f"[ERR] Création talk D-ID échouée : {e}")
        return

    # Poll until ready
    try:
        talk_data = poll_did_talk(talk_id)
        print("[RESULT] D-ID talk data:", json.dumps(talk_data, indent=2))
        # tenter d'extraire l'url vidéo
        video_url = talk_data.get("video_url") or talk_data.get("result", {}).get("video_url") or talk_data.get("output_url") or talk_data.get("video", {}).get("url")
        if video_url:
            print(f"[FIN] Vidéo disponible ici : {video_url}")
        else:
            print("[FIN] Vidéo traitée — consultez le JSON retourné ci-dessus pour le lien.")
    except Exception as e:
        print(f"[ERR] Erreur pendant le polling D-ID : {e}")
        return'''

if __name__ == "__main__":
    main()
