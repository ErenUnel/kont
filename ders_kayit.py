"""
İTÜ OBS otomatik ders kayıt modülü
- girisv3.itu.edu.tr üzerinden form tabanlı login
- obs.itu.edu.tr/api/ders-kayit/v21 üzerinden CRN ile ders ekleme
- Her denemede temiz oturum açarak timeout sorunlarını önleme

Gizli bilgiler ortam değişkeninden okunur:
  OBS_KULLANICI  -> İTÜ kullanıcı adı
  OBS_SIFRE      -> İTÜ şifresi
"""

import re
import time
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs

import requests
from bs4 import BeautifulSoup


# ================== SABİTLER ==================
GIRIS_URL = "https://girisv3.itu.edu.tr/Login.aspx"
DERS_KAYIT_API = "https://obs.itu.edu.tr/api/ders-kayit/v21"
OBS_DERS_KAYIT_SAYFA = "https://obs.itu.edu.tr/ogrenci/DersKayitIslemleri/DersKayit"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Kayıt denemesi ayarları
MAX_KAYIT_DENEME = 3
DENEME_ARASI_SN = 2
# =============================================


class OBSGirisHatasi(Exception):
    """OBS'ye giriş yapılamadığında fırlatılır."""
    pass


class DersKayitHatasi(Exception):
    """Ders kaydı başarısız olduğunda fırlatılır."""
    pass


# ================== YARDIMCILAR ==================

def _form_degerini_al(soup: BeautifulSoup, input_id: str) -> str:
    """ASP.NET form hidden field'larını (ViewState vb.) çeker."""
    eleman = soup.find("input", {"id": input_id})
    if eleman and "value" in eleman.attrs:
        return eleman["value"]
    return ""


def _sub_session_id_al(soup: BeautifulSoup) -> str:
    """Login formunun action URL'inden subSessionId'yi çeker."""
    form = soup.find("form", id="form1")
    if not form:
        raise OBSGirisHatasi("Login formu bulunamadı (form1 yok)")
    action = form.get("action", "")
    eslesme = re.search(r"subSessionId=([^&]+)", action)
    if eslesme:
        return eslesme.group(1)
    raise OBSGirisHatasi("subSessionId bulunamadı")


def _giris_url_olustur(sub_session_id: str) -> str:
    """subSessionId ve hedef sayfa ile login URL'i oluşturur."""
    parsed = urlparse(GIRIS_URL)
    params = {
        "subSessionId": sub_session_id,
        "currentURL": OBS_DERS_KAYIT_SAYFA,
    }
    query = urlencode(params)
    return urlunparse((
        parsed.scheme, parsed.netloc, parsed.path,
        parsed.params, query, parsed.fragment
    ))


# ================== ANA FONKSİYONLAR ==================

def giris_yap(kullanici: str, sifre: str) -> requests.Session:
    """
    İTÜ OBS'ye giriş yapar, oturumu döndürür.

    1. GET  girisv3.itu.edu.tr/Login.aspx -> form alanlarını çek
    2. POST girisv3.itu.edu.tr/Login.aspx -> login ol
    3. Yönlendirmeleri takip et -> OBS oturumu kur

    Returns:
        requests.Session: Aktif OBS oturumu
    Raises:
        OBSGirisHatasi: Giriş başarısız olursa
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. Login sayfasını çek, ASP.NET form alanlarını al
    print("[OBS] Giriş sayfası yükleniyor...")
    try:
        resp = session.get(GIRIS_URL, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OBSGirisHatasi(f"Giriş sayfası yüklenemedi: {e}")

    soup = BeautifulSoup(resp.text, "html.parser")

    # subSessionId'yi al
    try:
        sub_session_id = _sub_session_id_al(soup)
    except OBSGirisHatasi:
        # İlk yüklemede subSessionId olmayabilir, sayfayı yeniden yükle
        sub_session_id = ""

    # ASP.NET hidden field'ları
    viewstate = _form_degerini_al(soup, "__VIEWSTATE")
    viewstate_gen = _form_degerini_al(soup, "__VIEWSTATEGENERATOR")
    event_validation = _form_degerini_al(soup, "__EVENTVALIDATION")

    # 2. Login POST isteği
    login_url = _giris_url_olustur(sub_session_id) if sub_session_id else GIRIS_URL

    login_data = {
        "__VIEWSTATE": viewstate,
        "__VIEWSTATEGENERATOR": viewstate_gen,
        "__EVENTVALIDATION": event_validation,
        "ctl00$ContentPlaceHolder1$hfAppName": "",
        "ctl00$ContentPlaceHolder1$hfToken": "",
        "ctl00$ContentPlaceHolder1$hfCommand": "",
        "ctl00$ContentPlaceHolder1$tbUserName": kullanici,
        "ctl00$ContentPlaceHolder1$tbPassword": sifre,
        "ctl00$ContentPlaceHolder1$btnLogin": "Giriş / Login",
    }

    print("[OBS] Giriş yapılıyor...")
    try:
        resp = session.post(
            login_url,
            data=login_data,
            timeout=30,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OBSGirisHatasi(f"Login POST isteği başarısız: {e}")

    # Giriş başarılı mı kontrol et
    if "Login" in resp.url and "obs.itu.edu.tr" not in resp.url:
        # Hâlâ login sayfasındaysak giriş başarısız
        soup_sonuc = BeautifulSoup(resp.text, "html.parser")
        hata_mesaji = soup_sonuc.find("span", {"id": "ContentPlaceHolder1_lblStatus"})
        if hata_mesaji:
            raise OBSGirisHatasi(f"Giriş başarısız: {hata_mesaji.text.strip()}")
        raise OBSGirisHatasi("Giriş başarısız: Kullanıcı adı veya şifre hatalı olabilir")

    # 3. OBS'ye yönlendirmeyi takip et
    # Bölüm seçme sayfası kontrolü (tek bölüm varsa otomatik geçer)
    if "KimlikSecim" in resp.url or "identity-card" in resp.text:
        print("[OBS] Bölüm seçim sayfası tespit edildi, otomatik seçiliyor...")
        soup_bolum = BeautifulSoup(resp.text, "html.parser")
        identity_cards = soup_bolum.find_all("div", class_="identity-card")
        if identity_cards:
            link = identity_cards[0].find("a", class_="stretched-link")
            if link and "href" in link.attrs:
                bolum_url = "https://obs.itu.edu.tr" + link["href"]
                resp = session.get(bolum_url, timeout=20)

    # Ders kayıt sayfasına git (oturumun aktif olduğundan emin ol)
    try:
        resp_kayit = session.get(OBS_DERS_KAYIT_SAYFA, timeout=20)
        if resp_kayit.status_code == 200:
            print("[OBS] ✅ Giriş başarılı, ders kayıt sayfasına erişildi.")
        else:
            print(f"[OBS] ⚠️ Ders kayıt sayfası HTTP {resp_kayit.status_code}")
    except requests.RequestException:
        print("[OBS] ⚠️ Ders kayıt sayfası kontrol edilemedi, devam ediliyor...")

    return session


def ders_kaydet(session: requests.Session, crn: str) -> dict:
    """
    Aktif OBS oturumu ile CRN'ye göre ders kaydı yapar.

    Args:
        session: giris_yap() ile elde edilen aktif oturum
        crn: Kaydedilecek dersin CRN kodu

    Returns:
        dict: {
            "basarili": bool,
            "mesaj": str,       # Açıklama mesajı
            "detay": str | None # API'den dönen ham yanıt
        }
    """
    # API'ye ders ekleme isteği gönder
    kayit_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": OBS_DERS_KAYIT_SAYFA,
        "Origin": "https://obs.itu.edu.tr",
    }

    # Bearer token varsa cookie'den al (bazı versiyonlarda gerekir)
    for cookie in session.cookies:
        if cookie.name.lower() in ("access_token", "bearer", ".obsauth"):
            kayit_headers["Authorization"] = f"Bearer {cookie.value}"
            break

    payload = {"ECRN": [crn], "SCRN": []}

    try:
        resp = session.post(
            DERS_KAYIT_API,
            json=payload,
            headers=kayit_headers,
            timeout=30,
        )
    except requests.RequestException as e:
        return {
            "basarili": False,
            "mesaj": f"API isteği başarısız: {e}",
            "detay": None,
        }

    # Yanıtı yorumla
    detay_str = resp.text[:500] if resp.text else "(boş yanıt)"

    if resp.status_code == 200:
        try:
            veri = resp.json()
            # API başarılı yanıt döndüyse
            if isinstance(veri, dict):
                hatalar = veri.get("hataMesajlari", veri.get("errors", []))
                if hatalar:
                    hata_txt = "; ".join(str(h) for h in hatalar) if isinstance(hatalar, list) else str(hatalar)
                    return {
                        "basarili": False,
                        "mesaj": f"Kayıt reddedildi: {hata_txt}",
                        "detay": detay_str,
                    }
            return {
                "basarili": True,
                "mesaj": f"CRN {crn} başarıyla kaydedildi!",
                "detay": detay_str,
            }
        except ValueError:
            # JSON parse edilemedi ama 200 döndü
            if "başarı" in resp.text.lower() or "success" in resp.text.lower():
                return {
                    "basarili": True,
                    "mesaj": f"CRN {crn} kaydedildi (yanıt JSON değil ama başarılı görünüyor)",
                    "detay": detay_str,
                }
            return {
                "basarili": False,
                "mesaj": "Yanıt ayrıştırılamadı",
                "detay": detay_str,
            }

    elif resp.status_code == 401:
        return {
            "basarili": False,
            "mesaj": "Oturum süresi dolmuş (401 Unauthorized)",
            "detay": detay_str,
        }
    elif resp.status_code == 403:
        return {
            "basarili": False,
            "mesaj": "Erişim engellendi (403 Forbidden) - Kayıt dönemi dışı olabilir",
            "detay": detay_str,
        }
    else:
        return {
            "basarili": False,
            "mesaj": f"Beklenmeyen HTTP durumu: {resp.status_code}",
            "detay": detay_str,
        }


def kayit_dene(kullanici: str, sifre: str, crn: str) -> dict:
    """
    Tam kayıt akışı: giriş yap + ders kaydet.
    Başarısız olursa MAX_KAYIT_DENEME kez tekrar dener.

    Args:
        kullanici: İTÜ kullanıcı adı
        sifre: İTÜ şifresi
        crn: Kaydedilecek CRN

    Returns:
        dict: ders_kaydet() ile aynı format
    """
    son_hata = None

    for deneme in range(1, MAX_KAYIT_DENEME + 1):
        print(f"[OBS] Kayıt denemesi {deneme}/{MAX_KAYIT_DENEME} — CRN: {crn}")

        try:
            # Her denemede temiz oturum aç
            session = giris_yap(kullanici, sifre)
            sonuc = ders_kaydet(session, crn)

            if sonuc["basarili"]:
                return sonuc

            # Oturum düştüyse tekrar dene, diğer hatalarda dur
            if "401" in sonuc["mesaj"] or "oturum" in sonuc["mesaj"].lower():
                print(f"[OBS] Oturum hatası, tekrar deneniyor...")
                son_hata = sonuc
                time.sleep(DENEME_ARASI_SN)
                continue

            # Kontenjan dolu, önşart hatası vb. → tekrar denemenin anlamı yok
            return sonuc

        except OBSGirisHatasi as e:
            print(f"[OBS] Giriş hatası: {e}")
            son_hata = {
                "basarili": False,
                "mesaj": f"Giriş hatası: {e}",
                "detay": None,
            }
            time.sleep(DENEME_ARASI_SN)

        except Exception as e:
            print(f"[OBS] Beklenmeyen hata: {e}")
            son_hata = {
                "basarili": False,
                "mesaj": f"Beklenmeyen hata: {e}",
                "detay": None,
            }
            time.sleep(DENEME_ARASI_SN)

    return son_hata or {
        "basarili": False,
        "mesaj": "Tüm denemeler başarısız",
        "detay": None,
    }
