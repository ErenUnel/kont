"""
İTÜ OBS otomatik ders kayıt modülü
- Girişe OBS ders kayıt sayfasından başlanır; OBS doğru yönlendirmeyle
  girisv3.itu.edu.tr giriş formuna gönderir
- Giriş sonrası /ogrenci/auth/jwt adresinden Bearer token alınır
- obs.itu.edu.tr/api/ders-kayit/v21 üzerinden CRN ile ders eklenir
- Her denemede temiz oturum açılır

Gizli bilgiler ortam değişkeninden okunur:
  OBS_KULLANICI  -> İTÜ kullanıcı adı
  OBS_SIFRE      -> İTÜ şifresi
"""

import json
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ================== SABİTLER ==================
OBS_ALAN_ADI = "obs.itu.edu.tr"
DERS_KAYIT_API = "https://obs.itu.edu.tr/api/ders-kayit/v21"
OBS_DERS_KAYIT_SAYFA = "https://obs.itu.edu.tr/ogrenci/DersKayitIslemleri/DersKayit"
OBS_JWT_URL = "https://obs.itu.edu.tr/ogrenci/auth/jwt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

# API'nin döndürdüğü sonuç kodlarının açıklamaları (bilinenler)
SONUC_KODLARI = {
    "VAL06": "kontenjan dolu",
}

# Kayıt denemesi ayarları
MAX_KAYIT_DENEME = 3
DENEME_ARASI_SN = 2
# =============================================


class OBSGirisHatasi(Exception):
    """OBS'ye giriş yapılamadığında fırlatılır."""
    pass


# ================== YARDIMCILAR ==================

def _alan_adi(url: str) -> str:
    return urlparse(url).netloc.lower()


def _giris_hata_mesaji(html: str) -> str:
    """Giriş sayfasında gösterilen hata mesajını bulmaya çalışır."""
    soup = BeautifulSoup(html, "html.parser")
    for eleman in soup.find_all(True, id=True):
        eid = eleman["id"].lower()
        if "lblstatus" in eid or "lblerror" in eid or "lblmessage" in eid:
            metin = eleman.get_text(" ", strip=True)
            if metin:
                return metin
    uyari = soup.find(class_="alert")
    if uyari and uyari.get_text(strip=True):
        return uyari.get_text(" ", strip=True)
    return "Kullanıcı adı veya şifre hatalı olabilir"


def _token_ayikla(metin: str) -> str:
    """/ogrenci/auth/jwt yanıtından JWT'yi çıkarır (düz metin ya da JSON olabilir)."""
    metin = metin.strip()
    try:
        veri = json.loads(metin)
        if isinstance(veri, str):
            metin = veri
        elif isinstance(veri, dict):
            for anahtar in ("token", "accessToken", "access_token", "jwt"):
                if isinstance(veri.get(anahtar), str):
                    metin = veri[anahtar]
                    break
    except ValueError:
        pass
    metin = metin.strip().strip('"')
    if metin.count(".") != 2 or " " in metin or "<" in metin:
        raise OBSGirisHatasi(f"JWT alınamadı, beklenmeyen yanıt: {metin[:100]}")
    return metin


# ================== ANA FONKSİYONLAR ==================

def giris_yap(kullanici: str, sifre: str) -> tuple[requests.Session, str]:
    """
    İTÜ OBS'ye giriş yapar; (oturum, jwt_token) döndürür.

    1. GET  OBS ders kayıt sayfası -> girisv3 giriş formuna yönlendirilir
    2. POST giriş formu (tüm gizli alanlarla birlikte)
    3. GET  /ogrenci/auth/jwt -> API için Bearer token

    Raises:
        OBSGirisHatasi: Giriş başarısız olursa
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. OBS'den başla ki giriş sayfası doğru currentURL ile açılsın
    print("[OBS] Giriş sayfası yükleniyor...")
    try:
        resp = session.get(OBS_DERS_KAYIT_SAYFA, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OBSGirisHatasi(f"Giriş sayfası yüklenemedi: {e}")

    if _alan_adi(resp.url) != OBS_ALAN_ADI:
        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form", id="form1")
        if not form:
            raise OBSGirisHatasi(f"Login formu bulunamadı ({resp.url[:80]})")

        # 2. Formdaki tüm alanları olduğu gibi gönder, sadece kullanıcı/şifreyi doldur
        login_data = {}
        for inp in soup.find_all("input"):
            ad = inp.get("name")
            if ad and inp.get("type", "text").lower() not in ("submit", "button", "image"):
                login_data[ad] = inp.get("value", "")
        login_data["ctl00$ContentPlaceHolder1$tbUserName"] = kullanici
        login_data["ctl00$ContentPlaceHolder1$tbPassword"] = sifre
        buton = soup.find("input", {"name": "ctl00$ContentPlaceHolder1$btnLogin"})
        login_data["ctl00$ContentPlaceHolder1$btnLogin"] = (
            buton.get("value", "Giriş / Login") if buton else "Giriş / Login"
        )

        print("[OBS] Giriş yapılıyor...")
        try:
            resp = session.post(
                urljoin(resp.url, form.get("action", "")),
                data=login_data,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise OBSGirisHatasi(f"Login POST isteği başarısız: {e}")

        # Hâlâ OBS dışındaysak (girisv3'te kaldıysak) giriş başarısız
        if _alan_adi(resp.url) != OBS_ALAN_ADI:
            raise OBSGirisHatasi(f"Giriş başarısız: {_giris_hata_mesaji(resp.text)}")

    # Bölüm seçme sayfası (birden fazla bölüm varsa ilkini seç)
    if "KimlikSecim" in resp.url or "identity-card" in resp.text:
        print("[OBS] Bölüm seçim sayfası tespit edildi, otomatik seçiliyor...")
        soup_bolum = BeautifulSoup(resp.text, "html.parser")
        kart = soup_bolum.find("div", class_="identity-card")
        link = kart.find("a", class_="stretched-link") if kart else None
        if link and link.get("href"):
            session.get(urljoin(resp.url, link["href"]), timeout=20)

    # 3. API için JWT al
    try:
        resp_jwt = session.get(OBS_JWT_URL, timeout=20)
        resp_jwt.raise_for_status()
    except requests.RequestException as e:
        raise OBSGirisHatasi(f"JWT isteği başarısız: {e}")
    if _alan_adi(resp_jwt.url) != OBS_ALAN_ADI or "Login" in resp_jwt.url:
        raise OBSGirisHatasi("JWT alınamadı: oturum açılmamış görünüyor")

    token = _token_ayikla(resp_jwt.text)
    print("[OBS] ✅ Giriş başarılı, token alındı.")
    return session, token


def _sonucu_yorumla(veri, crn: str) -> tuple[bool, str] | None:
    """
    v21 yanıtındaki CRN sonucunu bulur. Beklenen biçim:
      {"ecrnResultList": [{"crn": "12575", "statusCode": 0, "resultCode": "..."}], ...}
    statusCode == 0 başarı demektir. Biçim tanınmazsa None döner.
    """
    if not isinstance(veri, dict):
        return None
    liste = veri.get("ecrnResultList")
    if not isinstance(liste, list):
        return None
    for sonuc in liste:
        if isinstance(sonuc, dict) and str(sonuc.get("crn", "")).strip() == crn:
            kod = sonuc.get("statusCode")
            aciklama = sonuc.get("resultCode") or sonuc.get("resultData") or ""
            if aciklama in SONUC_KODLARI:
                aciklama = f"{aciklama} ({SONUC_KODLARI[aciklama]})"
            return kod == 0, f"statusCode={kod} {aciklama}".strip()
    return None


def ders_kaydet(session: requests.Session, token: str, crn: str) -> dict:
    """
    Aktif OBS oturumu ve token ile CRN'ye göre ders kaydı yapar.

    Returns:
        dict: {"basarili": bool, "mesaj": str, "detay": str | None}
    """
    kayit_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}",
        "Referer": OBS_DERS_KAYIT_SAYFA,
        "Origin": "https://obs.itu.edu.tr",
    }
    payload = {"ECRN": [crn], "SCRN": []}

    try:
        resp = session.post(DERS_KAYIT_API, json=payload, headers=kayit_headers, timeout=30)
    except requests.RequestException as e:
        return {"basarili": False, "mesaj": f"API isteği başarısız: {e}", "detay": None}

    detay_str = resp.text[:500] if resp.text else "(boş yanıt)"
    print(f"[OBS] API yanıtı HTTP {resp.status_code}: {detay_str}")

    if resp.status_code == 401:
        return {"basarili": False, "mesaj": "Oturum süresi dolmuş (401 Unauthorized)", "detay": detay_str}
    if resp.status_code == 403:
        return {"basarili": False, "mesaj": "Erişim engellendi (403) - Kayıt dönemi dışı olabilir", "detay": detay_str}
    if resp.status_code != 200:
        return {"basarili": False, "mesaj": f"Beklenmeyen HTTP durumu: {resp.status_code}", "detay": detay_str}

    try:
        veri = resp.json()
    except ValueError:
        return {"basarili": False, "mesaj": "Yanıt JSON değil, kayıt doğrulanamadı", "detay": detay_str}

    yorum = _sonucu_yorumla(veri, crn)
    if yorum is None:
        # Emin olamıyorsak başarılı SAYMA: bot denemeye devam etsin, ham yanıtı bildirsin
        return {
            "basarili": False,
            "mesaj": f"Yanıt biçimi tanınmadı, OBS'den elle kontrol et: {detay_str[:200]}",
            "detay": detay_str,
        }

    basarili, aciklama = yorum
    if basarili:
        return {"basarili": True, "mesaj": f"CRN {crn} kaydedildi ({aciklama})", "detay": detay_str}
    return {"basarili": False, "mesaj": f"Kayıt reddedildi: {aciklama}", "detay": detay_str}


def kayit_dene(kullanici: str, sifre: str, crn: str) -> dict:
    """
    Tam kayıt akışı: giriş yap + ders kaydet.
    Giriş/oturum hatalarında MAX_KAYIT_DENEME kez tekrar dener.

    Returns:
        dict: ders_kaydet() ile aynı format
    """
    son_hata = None

    for deneme in range(1, MAX_KAYIT_DENEME + 1):
        print(f"[OBS] Kayıt denemesi {deneme}/{MAX_KAYIT_DENEME} — CRN: {crn}")

        try:
            session, token = giris_yap(kullanici, sifre)
            sonuc = ders_kaydet(session, token, crn)

            if sonuc["basarili"]:
                return sonuc

            # Oturum/bağlantı sorunuysa tekrar dene, diğer hatalarda dur
            if "401" in sonuc["mesaj"] or "API isteği başarısız" in sonuc["mesaj"]:
                print("[OBS] Oturum/bağlantı hatası, tekrar deneniyor...")
                son_hata = sonuc
                time.sleep(DENEME_ARASI_SN)
                continue

            # Kontenjan dolu, önşart hatası vb. → hemen tekrar denemenin anlamı yok
            return sonuc

        except OBSGirisHatasi as e:
            print(f"[OBS] Giriş hatası: {e}")
            son_hata = {"basarili": False, "mesaj": f"Giriş hatası: {e}", "detay": None}
            # Şifre yanlışsa tekrar tekrar deneyip hesabı kilitletme
            if "Giriş başarısız" in str(e):
                return son_hata
            time.sleep(DENEME_ARASI_SN)

        except Exception as e:
            print(f"[OBS] Beklenmeyen hata: {e}")
            son_hata = {"basarili": False, "mesaj": f"Beklenmeyen hata: {e}", "detay": None}
            time.sleep(DENEME_ARASI_SN)

    return son_hata or {"basarili": False, "mesaj": "Tüm denemeler başarısız", "detay": None}
