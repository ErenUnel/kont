"""
İTÜ OBS kontenjan takip botu (GitHub Actions uyumlu)
- Belirlenen CRN'lerin kontenjanını ve yazılan sayısını dakikada bir kontrol eder
- Boş yer varsa:
  * oto_kayit aktifse → OBS'ye giriş yapıp dersi otomatik kaydetmeyi dener
    (başarısız olursa yer açık kaldığı sürece her turda tekrar dener)
  * değilse → sadece bildirim gönderir (ntfy veya Telegram)

Gizli bilgiler ortam değişkeninden okunur:
  NTFY_KONU                        -> ntfy konu adı
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID -> Telegram için
  OBS_KULLANICI, OBS_SIFRE         -> otomatik kayıt için
"""

import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from ders_kayit import kayit_dene

# ================== AYARLAR ==================
TEMEL_URL = "https://obs.itu.edu.tr/public/DersProgram/DersProgramSearch?programSeviyeTipiAnahtari=LS&dersBransKoduId="

# Taranacak ders listeleri (URL'nin sonundaki numara ders kodunu belirler)
DERS_URLLERI = [
    TEMEL_URL + "3",              # BLG
]

# ============ TAKİP KONFİGÜRASYONU ============
# Her CRN için: ntfy konusu ve otomatik kayıt açık/kapalı
# oto_kayit: True  → kontenjan açılınca otomatik kayıt dene
# oto_kayit: False → sadece bildirim gönder
TAKIP_CONFIG = {
    "12575": {"ntfy": os.getenv("NTFY_KONU", ""),  "oto_kayit": True},   # Computer Vision
}

TAKIP_EDILEN_CRNLER = list(TAKIP_CONFIG)

KONTROL_ARALIGI_SN = 60  # 60 sn'nin altına inme

# Kaç dakika sonra kapansın (GitHub Actions için). 0 = sonsuza kadar çalış
CALISMA_SURESI_DK = int(os.getenv("CALISMA_SURESI_DK", "0"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# OBS giriş bilgileri (otomatik kayıt için)
OBS_KULLANICI = os.getenv("OBS_KULLANICI", "")
OBS_SIFRE = os.getenv("OBS_SIFRE", "")
# =============================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (kontenjan-takip, kisisel kullanim)",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://obs.itu.edu.tr/public/DersProgram",
}
TR_SAATI = timezone(timedelta(hours=3))

# Başarıyla kaydedilen CRN'ler (tekrar denenmez)
KAYDEDILEN_CRNLER = set()

# Şifre yanlışsa otomatik kayıt bu çalışma boyunca kapatılır (hesap kilitlenmesin)
oto_kayit_kapali = False


def bildirim_gonder(mesaj: str, crn: str = "") -> None:
    """crn verilirse sadece o dersin konusuna, verilmezse tum konulara gonderir."""
    print(f"[BİLDİRİM] {mesaj}", flush=True)

    if crn:
        konular = [TAKIP_CONFIG.get(crn, {}).get("ntfy", "")]
    else:
        konular = list(dict.fromkeys(
            cfg["ntfy"] for cfg in TAKIP_CONFIG.values()
        ))

    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj},
                timeout=15,
            )
        except requests.RequestException as e:
            print(f"[!] Telegram bildirimi gönderilemedi: {e}")

    for konu in konular:
        if not konu:
            continue
        try:
            requests.post(
                f"https://ntfy.sh/{konu}",
                data=mesaj.encode("utf-8"),
                headers={"Priority": "urgent", "Tags": "rotating_light"},
                timeout=15,
            )
        except requests.RequestException as e:
            print(f"[!] ntfy bildirimi gönderilemedi: {e}")


def kontenjanlari_cek() -> dict:
    """Tum listeleri tarar; {crn: (ders_adi, kontenjan, yazilan)} döndürür."""
    sonuc = {}
    for url in DERS_URLLERI:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        for satir in soup.find_all("tr"):
            hucreler = [td.get_text(" ", strip=True) for td in satir.find_all("td")]
            if len(hucreler) < 11:
                continue
            crn = hucreler[0]
            if crn in TAKIP_EDILEN_CRNLER:
                # Sütunlar: 0 CRN, 1 Kod, 2 Ad, ..., 9 Kontenjan, 10 Yazılan
                try:
                    sonuc[crn] = (hucreler[2], int(hucreler[9]), int(hucreler[10]))
                except ValueError:
                    pass
    return sonuc


def otomatik_kayit_dene(crn: str, ders_adi: str) -> str | None:
    """
    Kontenjan açık olan CRN için otomatik ders kaydı dener.
    Bildirimde gösterilecek sonuç metnini döndürür; deneme yapılmadıysa None.
    """
    global oto_kayit_kapali

    if crn in KAYDEDILEN_CRNLER or oto_kayit_kapali:
        return None
    if not TAKIP_CONFIG.get(crn, {}).get("oto_kayit", False):
        return None

    if not OBS_KULLANICI or not OBS_SIFRE:
        return ("⚠️ Otomatik kayıt yapılamadı: OBS_KULLANICI veya OBS_SIFRE tanımlı değil.\n"
                "Elle kayıt yapmanız gerekiyor!")

    sonuc = kayit_dene(OBS_KULLANICI, OBS_SIFRE, crn)

    if sonuc["basarili"]:
        KAYDEDILEN_CRNLER.add(crn)
        return f"✅ DERS KAYDEDİLDİ!\n{sonuc['mesaj']}\n(OBS'den kontrol etmeyi unutma)"

    if "Giriş başarısız" in sonuc["mesaj"]:
        oto_kayit_kapali = True
        return (f"❌ OBS girişi başarısız, otomatik kayıt bu çalışmada kapatıldı!\n"
                f"{sonuc['mesaj']}\nOBS_KULLANICI / OBS_SIFRE secret'larını kontrol et.")

    return (f"❌ Otomatik kayıt başarısız (yer açık kaldıkça tekrar denenecek)\n"
            f"Hata: {sonuc['mesaj']}\nElle kayıt yapmayı deneyin!")


def main() -> None:
    oto_kayit_var = any(cfg.get("oto_kayit") for cfg in TAKIP_CONFIG.values())

    for crn, cfg in TAKIP_CONFIG.items():
        if not cfg.get("ntfy"):
            print(f"[!] {crn} için ntfy konusu tanımlı değil, bildirim gitmeyecek.")

    if oto_kayit_var and (not OBS_KULLANICI or not OBS_SIFRE):
        print("[!] ⚠️ Otomatik kayıt aktif CRN'ler var ama OBS_KULLANICI/OBS_SIFRE tanımlı değil!")
        print("[!] Otomatik kayıt çalışmayacak, sadece bildirim gönderilecek.")

    if oto_kayit_var and OBS_KULLANICI:
        oto_crnler = [c for c, cfg in TAKIP_CONFIG.items() if cfg.get("oto_kayit")]
        print(f"[*] Otomatik kayıt aktif CRN'ler: {', '.join(oto_crnler)}")

    bitis = time.time() + CALISMA_SURESI_DK * 60 if CALISMA_SURESI_DK else None

    if not bitis:
        basla_mesaj = "✅ Kontenjan botu başladı: " + ", ".join(TAKIP_EDILEN_CRNLER)
        if oto_kayit_var and OBS_KULLANICI:
            basla_mesaj += "\n🤖 Otomatik kayıt: AKTİF"
        bildirim_gonder(basla_mesaj)

    onceki = {}
    son_kayit_mesaji = {}   # aynı hata mesajını her dakika tekrar göndermemek için
    ardisik_hata = 0

    while bitis is None or time.time() < bitis:
        zaman = datetime.now(TR_SAATI).strftime("%H:%M:%S")
        try:
            veri = kontenjanlari_cek()
            ardisik_hata = 0

            if not veri:
                print(f"[{zaman}] Uyarı: CRN'ler sayfada bulunamadı.")

            for crn, (ad, kont, yaz) in veri.items():
                print(f"[{zaman}] {crn} {ad}: {yaz}/{kont}", flush=True)
                yer_var = yaz < kont
                eski = onceki.get(crn)

                olay = None
                if eski:
                    _, eski_kont, eski_yaz = eski
                    if kont > eski_kont:
                        olay = (f"🚨 KONTENJAN ARTTI!\n{crn} {ad}\n"
                                f"{eski_kont} → {kont} (yazılan: {yaz})")
                    elif yer_var and not (eski_yaz < eski_kont):
                        olay = f"🟢 BOŞ YER AÇILDI!\n{crn} {ad}\nDurum: {yaz}/{kont}"
                elif yer_var:
                    olay = f"🟢 Şu an yer var: {crn} {ad} ({yaz}/{kont})"

                # Yer açıksa önce kaydı dene (hız önemli), sonra tek bildirim gönder
                kayit_mesaji = otomatik_kayit_dene(crn, ad) if yer_var else None
                if not yer_var:
                    son_kayit_mesaji.pop(crn, None)

                if olay:
                    bildirim_gonder(olay + (f"\n\n{kayit_mesaji}" if kayit_mesaji else ""), crn)
                elif kayit_mesaji and kayit_mesaji != son_kayit_mesaji.get(crn):
                    bildirim_gonder(f"{crn} {ad} ({yaz}/{kont})\n{kayit_mesaji}", crn)

                if kayit_mesaji:
                    son_kayit_mesaji[crn] = kayit_mesaji
                onceki[crn] = (ad, kont, yaz)

        except Exception as e:
            ardisik_hata += 1
            print(f"[{zaman}] Hata: {e}", flush=True)
            if ardisik_hata == 5:
                bildirim_gonder(f"⚠️ Bot 5 kez üst üste hata aldı: {e}")

        time.sleep(KONTROL_ARALIGI_SN + random.randint(0, 15))


if __name__ == "__main__":
    main()
